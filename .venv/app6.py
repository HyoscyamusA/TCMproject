from flask import Flask, request, render_template, jsonify, redirect, url_for
from neo4j import GraphDatabase
import os
import markdown
import sqlite3
import requests
import re
import json

app = Flask(__name__)

# ================= 1. 配置区域 =================

NEO4J_URI = "bolt://localhost:7687"
NEO4J_AUTH = ("neo4j", "12345678")  # ⚠️ 确认密码
DB_PATH = os.path.join(os.path.dirname(__file__), 'TCM.db')

# 颜色配置
COLOR_MAP = {
    "Prescription": "#87EBC1", "Disease": "#EE90A1", "Symptom": "#FF9F40",
    "Herb": "#69B2FF", "Source": "#9A66E4", "Literature": "#9A66E4",
    "Category": "#FFC0CB", "Efficacy": "#16C2D5", "Origin": "#B37FEB",
    "Contraindication": "#FF4D4F", "Flavor": "#FFD700", "Meridian": "#DA70D6",
    "方剂": "#87EBC1", "中药": "#69B2FF"
}

driver = GraphDatabase.driver(NEO4J_URI, auth=NEO4J_AUTH)


# ================= 2. 数据库与工具函数 =================

def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute('''CREATE TABLE IF NOT EXISTS query_history
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  query TEXT NOT NULL, mode TEXT NOT NULL,
                  timestamp DATETIME DEFAULT CURRENT_TIMESTAMP, has_result BOOLEAN)''')
    conn.commit()
    conn.close()


init_db()


def save_history(query, mode, has_result=True):
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("INSERT INTO query_history (query, mode, has_result) VALUES (?, ?, ?)",
                     (query, mode, has_result))
        conn.commit()
        conn.close()
    except:
        pass


def get_history(mode):
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("SELECT * FROM query_history WHERE mode = ? ORDER BY timestamp DESC LIMIT 20", (mode,))
        return c.fetchall()
    except:
        return []


def call_ollama(prompt, model="qwen2.5:1.5b"):
    url = "http://localhost:11434/api/generate"
    try:
        response = requests.post(url, json={"model": model, "prompt": prompt, "stream": False}, timeout=30)
        if response.status_code == 200: return response.json().get("response", "")
        return f"AI 响应错误: {response.status_code}"
    except:
        return "无法连接本地 AI 服务"


def format_node(node, category_override=None):
    """格式化节点并提取全量属性"""
    labels = list(node.labels)
    category = category_override if category_override else (labels[0] if labels else "Unknown")
    name = node.get('name', f"Node-{node.element_id}")
    color = COLOR_MAP.get(category, "#cccccc")
    size = 50 if category == "Prescription" else (35 if category == "Herb" else 30)

    return {
        "id": str(node.element_id),
        "name": name,
        "category": category,
        "symbolSize": size,
        "itemStyle": {"color": color},
        "attributes": dict(node)  # 全量属性
    }


# ================= 3. 路由逻辑 =================

@app.route('/')
def index(): return redirect(url_for('home'))


@app.route('/home')
def home(): return render_template('home.html', active_page='home')


# --- 功能 1：问答模式 (仅查症状/疾病) ---
@app.route('/answer', methods=['GET', 'POST'])
def answer():
    graph_data = {}
    table_data = []
    search_keyword = ""

    if request.method == 'POST':
        raw_input = request.form.get('symptom', '').strip()
        search_keyword = raw_input
        mode = "answer"

        if raw_input:
            # 🔥 严格限制：只匹配 Disease (包含症状描述)
            # 不再匹配 Prescription
            cypher_query = """
            MATCH (d:Disease) 
            WHERE d.name CONTAINS $symptom

            // 找到治疗该疾病的方剂
            MATCH (p:Prescription)-[:TREATS]->(d)

            // 找到方剂的组成 (用于表格和图谱)
            MATCH (p)-[r_comp:COMPOSED_OF]->(h:Herb)

            // 找到方剂的出处 (用于表格和图谱)
            OPTIONAL MATCH (p)-[:HAS_SOURCE]->(src)

            // 找到疾病关联的症状 (丰富图谱)
            OPTIONAL MATCH (d)-[:HAS_SYMPTOM]->(sym:Symptom)

            RETURN p, d, h, r_comp, src, sym
            LIMIT 150
            """

            with driver.session() as session:
                records = list(session.run(cypher_query, symptom=search_keyword))

            if records:
                nodes_dict = {}
                links = []
                categories = set()

                for rec in records:
                    # 节点格式化与去重
                    p_fmt = format_node(rec['p'], "Prescription")
                    nodes_dict[p_fmt['id']] = p_fmt
                    categories.add("Prescription")

                    d_fmt = format_node(rec['d'], "Disease")
                    nodes_dict[d_fmt['id']] = d_fmt
                    categories.add("Disease")

                    h_fmt = format_node(rec['h'], "Herb")
                    nodes_dict[h_fmt['id']] = h_fmt
                    categories.add("Herb")

                    # 连线
                    links.append({"source": p_fmt['id'], "target": d_fmt['id'], "name": "治疗"})
                    links.append(
                        {"source": p_fmt['id'], "target": h_fmt['id'], "name": rec['r_comp'].get('dosage', '组成')})

                    if rec['src']:
                        src_fmt = format_node(rec['src'], "Source")
                        nodes_dict[src_fmt['id']] = src_fmt
                        categories.add("Source")
                        links.append({"source": p_fmt['id'], "target": src_fmt['id'], "name": "出处"})

                    if rec['sym']:
                        sym_fmt = format_node(rec['sym'], "Symptom")
                        nodes_dict[sym_fmt['id']] = sym_fmt
                        categories.add("Symptom")
                        links.append({"source": d_fmt['id'], "target": sym_fmt['id'], "name": "症状"})

                    # 表格数据构建
                    table_data.append({
                        "方名": rec['p'].get('name'),
                        "出处": rec['src'].get('name', '未知') if rec['src'] else '未知',
                        "主治疾病": rec['d'].get('name'),
                        "中药": rec['h'].get('name'),
                        "剂量": rec['r_comp'].get('dosage', '适量')
                    })

                graph_data = {
                    "nodes": list(nodes_dict.values()),
                    "links": links,
                    "categories": [{"name": c} for c in list(categories)]
                }
                save_history(raw_input, mode, True)
            else:
                save_history(raw_input, mode, False)

    return render_template('answer.html',
                           graph_data=graph_data,
                           table_data=table_data,
                           search_keyword=search_keyword,
                           active_page='answer')


@app.route('/ask_ollama', methods=['POST'])
def ask_ollama():
    symptom = request.form.get('symptom')
    prompt = f"你是中医专家。用户查询病症：“{symptom}”。请分析病因病机，并推荐一个对症的经典方剂。纯文本回答。"
    return jsonify({"result": call_ollama(prompt)})


# --- 功能 2：推理模式 (方剂专用：单方全览 + 双方对比) ---
@app.route('/inference', methods=['GET', 'POST'])
def inference():
    graph_data = {}
    search_keyword = ""

    if request.method == 'POST':
        query = request.form.get('symptom', '').strip()
        search_keyword = query
        mode = "inference"

        # 拆分输入
        names = re.split(r'\s+|和|与', query)
        names = [n for n in names if n]

        cypher = ""
        params = {}

        # 🔥 情况 A：查单个方剂 (展示所有一度关系，包括出处、分类、组成等)
        if len(names) == 1:
            cypher = """
            MATCH (p:Prescription {name: $name})
            OPTIONAL MATCH (p)-[r]->(n)
            RETURN p, r, n
            LIMIT 200
            """
            params = {"name": names[0]}

        # 🔥 情况 B：查两个方剂 (对比模式，只展示主要关系)
        elif len(names) >= 2:
            cypher = """
            MATCH (p:Prescription) WHERE p.name IN [$n1, $n2]
            OPTIONAL MATCH (p)-[r]->(n)
            WHERE type(r) IN ['COMPOSED_OF', 'TREATS', 'HAS_SOURCE', 'HAS_CATEGORY']
            RETURN p, r, n
            LIMIT 200
            """
            params = {"n1": names[0], "n2": names[1]}

        if cypher:
            with driver.session() as session:
                records = list(session.run(cypher, **params))

            if records:
                nodes_dict = {}
                links = []
                categories = set()

                for rec in records:
                    if not rec['p']: continue

                    # 1. 中心方剂节点
                    p_fmt = format_node(rec['p'], "Prescription")
                    nodes_dict[p_fmt['id']] = p_fmt
                    categories.add("Prescription")

                    target = rec['n']
                    rel = rec['r']

                    if target:
                        # 2. 关联节点 (自动识别类别)
                        t_fmt = format_node(target)
                        nodes_dict[t_fmt['id']] = t_fmt
                        categories.add(t_fmt['category'])

                        # 3. 关系处理
                        rel_type = rel.type
                        rel_show = rel_type
                        # 汉化常用关系
                        if rel_type == "COMPOSED_OF":
                            rel_show = rel.get('dosage', '组成')
                        elif rel_type == "TREATS":
                            rel_show = "主治"
                        elif rel_type == "HAS_SOURCE":
                            rel_show = "出处"
                        elif rel_type == "HAS_CATEGORY":
                            rel_show = "分类"

                        links.append({
                            "source": p_fmt['id'],
                            "target": t_fmt['id'],
                            "name": rel_show,
                            "attributes": dict(rel)  # 关系属性
                        })

                graph_data = {
                    "nodes": list(nodes_dict.values()),
                    "links": links,
                    "categories": [{"name": c} for c in list(categories)]
                }
                save_history(query, mode, True)
            else:
                save_history(query, mode, False)
        else:
            save_history(query, mode, False)

    return render_template('inference.html',
                           graph_data=graph_data,
                           search_keyword=search_keyword,
                           active_page='inference')


@app.route('/suggest', methods=['POST'])
def suggest():
    symptom = request.form.get('symptom')
    prompt = f"请作为中医专家分析方剂：{symptom}。单方剂请分析【组成、功效、主治】；双方剂请分析【配伍异同】。"
    return markdown.markdown(call_ollama(prompt))


# --- 其他路由保持不变 (Herb Search, Dashboard 等) ---
@app.route('/herb_search', methods=['GET', 'POST'])
def herb_search():
    graph_data = {}
    herb_info = {}
    if request.method == 'POST':
        name = request.form.get('herb_name', '').strip()
        if name:
            cypher = "MATCH (h:Herb {name: $name}) OPTIONAL MATCH (h)-[r]->(n) RETURN h, type(r) as rel, n LIMIT 100"
            with driver.session() as session:
                records = list(session.run(cypher, name=name))
            if records:
                main = records[0]['h']
                herb_info = dict(main)
                nodes_dict = {main.element_id: format_node(main, "Herb")}
                links = []
                categories = {"Herb"}
                for rec in records:
                    target = rec['n']
                    if target:
                        t_fmt = format_node(target)
                        nodes_dict[t_fmt['id']] = t_fmt
                        categories.add(t_fmt['category'])
                        links.append(
                            {"source": str(main.element_id), "target": str(target.element_id), "name": rec['rel']})
                graph_data = {"nodes": list(nodes_dict.values()), "links": links,
                              "categories": [{"name": c} for c in list(categories)]}
                save_history(name, "herb", True)
            else:
                save_history(name, "herb", False)
    return render_template('herb_search.html', graph_data=graph_data, herb_info=herb_info, active_page='herb')


@app.route('/dashboard')
def dashboard():
    with driver.session() as session:
        c = session.run(
            "MATCH (h:Herb) WITH count(h) as c1 MATCH (p:Prescription) WITH c1, count(p) as c2 MATCH (d:Disease) WITH c1, c2, count(d) as c3 MATCH ()-[r]->() RETURN c1, c2, c3, count(r) as c4").single()
        eff = session.run(
            "MATCH (h:Herb)-[:HAS_EFFICACY]->(e:Efficacy) RETURN e.name as n, count(h) as c ORDER BY c DESC LIMIT 10")
        herb = session.run(
            "MATCH (p:Prescription)-[:COMPOSED_OF]->(h:Herb) RETURN h.name as n, count(p) as c ORDER BY c DESC LIMIT 10")
    return render_template('dashboard.html', total_herbs=c['c1'], total_prescriptions=c['c2'], total_diseases=c['c3'],
                           total_relations=c['c4'],
                           efficacy_data=[{"name": r['n'], "value": r['c']} for r in eff],
                           top_herbs_names=[r['n'] for r in herb], top_herbs_counts=[r['c'] for r in herb],
                           active_page='dashboard')


@app.route('/wordcloud')
def wordcloud(): return render_template('wordcloud.html', active_page='wordcloud')


@app.route('/api/wordcloud_data')
def wordcloud_data():
    with driver.session() as session:
        r = session.run(
            "MATCH (h:Herb)-[:HAS_EFFICACY]->(e:Efficacy) RETURN e.name AS name, COUNT(h) AS value ORDER BY value DESC LIMIT 100")
        return jsonify([{"name": x["name"], "value": x["value"]} for x in r])


@app.route('/history')
def history_page():
    return render_template('history.html', inference_history=get_history('inference'),
                           answer_history=get_history('answer'), herb_history=get_history('herb'),
                           active_page='history')


@app.route('/clear_history', methods=['POST'])
def clear_history_route():
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute('DELETE FROM query_history WHERE mode = ?', (request.form.get('mode'),))
        conn.commit()
        conn.close()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


if __name__ == '__main__':
    app.run(debug=True, port=5000)