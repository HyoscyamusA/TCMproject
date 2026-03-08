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

# Neo4j 数据库配置
NEO4J_URI = "bolt://localhost:7687"
NEO4J_AUTH = ("neo4j", "12345678")  # ⚠️ 确认密码

# SQLite 数据库路径
DB_PATH = os.path.join(os.path.dirname(__file__), 'TCM.db')

# 颜色配置表 (ECharts)
COLOR_MAP = {
    "Prescription": "#87EBC1",  # 方剂 (绿)
    "Disease": "#EE90A1",  # 疾病 (粉)
    "Symptom": "#FF9F40",  # 症状 (橙)
    "Herb": "#69B2FF",  # 中药 (蓝)
    "Source": "#9A66E4",  # 出处 (紫)
    "Literature": "#9A66E4",  # 文献
    "Category": "#FFC0CB",  # 分类
    "Efficacy": "#16C2D5",  # 功效
    "Origin": "#B37FEB",  # 产地
    "Contraindication": "#FF4D4F",  # 禁忌
    # 兼容中文
    "方剂": "#87EBC1",
    "中药": "#69B2FF"
}

# 初始化驱动
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
    """把 Neo4j 节点格式化为 ECharts 节点，包含所有属性"""
    labels = list(node.labels)
    category = category_override if category_override else (labels[0] if labels else "Unknown")
    name = node.get('name', f"Node-{node.element_id}")
    color = COLOR_MAP.get(category, "#cccccc")

    # 大小调整
    size = 50 if category == "Prescription" else (35 if category == "Herb" else 30)

    return {
        "id": str(node.element_id),
        "name": name,
        "category": category,
        "symbolSize": size,
        "itemStyle": {"color": color},
        "attributes": dict(node)  # 🔥 核心：全量属性
    }


# ================= 3. 路由逻辑 =================

@app.route('/')
def index(): return redirect(url_for('home'))


@app.route('/home')
def home(): return render_template('home.html', active_page='home')


# --- 功能 1：问答模式 (纯净版：只查症状/疾病) ---
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
            # 🔥 修改点：移除 OR p.name CONTAINS，只查疾病/症状
            cypher_query = """
            MATCH (d:Disease) WHERE d.name CONTAINS $symptom
            MATCH (p:Prescription)-[:TREATS]->(d)

            // 查方剂组成 (用于显示，但不做为搜索条件)
            MATCH (p)-[r_comp:COMPOSED_OF]->(h:Herb)

            // 查关联症状
            OPTIONAL MATCH (d)-[:HAS_SYMPTOM]->(sym:Symptom)

            RETURN p, d, h, r_comp, sym
            LIMIT 100
            """

            with driver.session() as session:
                records = list(session.run(cypher_query, symptom=search_keyword))

            if records:
                nodes_dict = {}
                links = []
                categories = set()

                for rec in records:
                    # 节点处理
                    p_id = format_node(rec['p'], "Prescription")['id']
                    nodes_dict[p_id] = format_node(rec['p'], "Prescription")
                    categories.add("Prescription")

                    d_id = format_node(rec['d'], "Disease")['id']
                    nodes_dict[d_id] = format_node(rec['d'], "Disease")
                    categories.add("Disease")

                    h_id = format_node(rec['h'], "Herb")['id']
                    nodes_dict[h_id] = format_node(rec['h'], "Herb")
                    categories.add("Herb")

                    # 连线
                    links.append({"source": p_id, "target": d_id, "name": "治疗"})
                    links.append({"source": p_id, "target": h_id, "name": rec['r_comp'].get('dosage', '组成')})

                    if rec['sym']:
                        sym_fmt = format_node(rec['sym'], "Symptom")
                        nodes_dict[sym_fmt['id']] = sym_fmt
                        categories.add("Symptom")
                        links.append({"source": d_id, "target": sym_fmt['id'], "name": "症状"})

                    # 表格数据
                    table_data.append({
                        "方名": rec['p'].get('name'),
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
    prompt = f"你是中医专家。用户症状：“{symptom}”。请分析病因并推荐1个方剂。纯文本回答。"
    return jsonify({"result": call_ollama(prompt)})


# --- 功能 2：推理模式 (核心修改：支持单方剂 & 双方剂) ---
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

        # 🔥 情况 A：查单个方剂 (全详情)
        if len(names) == 1:
            cypher = """
            MATCH (p:Prescription {name: $name})
            OPTIONAL MATCH (p)-[r]->(n)
            RETURN p, r, n
            LIMIT 200
            """
            params = {"name": names[0]}

        # 🔥 情况 B：查两个方剂 (对比)
        elif len(names) >= 2:
            cypher = """
            MATCH (p:Prescription) WHERE p.name IN [$n1, $n2]
            OPTIONAL MATCH (p)-[r]->(n)
            // 这里只展示它们的组成、出处、主治，避免图太大
            WHERE type(r) IN ['COMPOSED_OF', 'TREATS', 'HAS_SOURCE']
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

                    # 中心方剂
                    p_fmt = format_node(rec['p'], "Prescription")
                    nodes_dict[p_fmt['id']] = p_fmt
                    categories.add("Prescription")

                    target = rec['n']
                    rel = rec['r']

                    if target:
                        t_fmt = format_node(target)  # 自动识别类别 (Herb, Disease, Source...)
                        nodes_dict[t_fmt['id']] = t_fmt
                        categories.add(t_fmt['category'])

                        # 翻译关系名
                        rel_type = rel.type
                        rel_show = rel_type
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
                            "attributes": dict(rel)
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
    """推理模式 AI 分析"""
    symptom = request.form.get('symptom')
    # 根据输入内容动态调整 Prompt
    prompt = f"请作为中医专家分析：{symptom}。如果是单个方剂，请详细介绍其【功效】、【主治】和【禁忌】；如果是两个方剂，请对比它们的【异同点】和【能否合用】。"
    return markdown.markdown(call_ollama(prompt))


# --- 功能 3：其他路由 (保持不变) ---
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