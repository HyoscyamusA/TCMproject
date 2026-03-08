from flask import Flask, request, render_template, jsonify, redirect, url_for
from neo4j import GraphDatabase
import os
import markdown
from markupsafe import Markup
import sqlite3
from datetime import datetime
import requests
import json
import re

app = Flask(__name__)

# ================= 配置区 =================
NEO4J_URI = "bolt://localhost:7687"
NEO4J_AUTH = ("neo4j", "12345678")  # ⚠️ 确保密码正确
DB_PATH = os.path.join(os.path.dirname(__file__), 'TCM.db')

# 颜色配置
COLOR_MAP = {
    "Prescription": "#87EBC1",  # 方剂
    "Disease": "#EE90A1",  # 疾病
    "Herb": "#69B2FF",  # 中药
    "Category": "#FF9F40",  # 类别
    "Efficacy": "#16C2D5",  # 功效
    "Literature": "#9A66E4",  # 文献
    "Flavor": "#FFC0CB",  # 性味
    "Meridian": "#DDA0DD",  # 归经
    "Origin": "#B37FEB",  # 产地

    # 兼容中文键名（防止个别地方没改过来）
    "方剂": "#87EBC1",
    "功能主治": "#EE90A1",
    "中药名": "#69B2FF"
}


# ================= 数据库辅助函数 =================

def get_driver():
    return GraphDatabase.driver(NEO4J_URI, auth=NEO4J_AUTH)


driver = get_driver()


def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    # 重新建表，包含 has_result 字段
    c.execute('''CREATE TABLE IF NOT EXISTS query_history
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  query TEXT NOT NULL,
                  mode TEXT NOT NULL,
                  timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                  has_result BOOLEAN)''')
    conn.commit()
    conn.close()


init_db()


def save_history(query, mode, has_result=True):
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("INSERT INTO query_history (query, mode, has_result) VALUES (?, ?, ?)",
                  (query, mode, has_result))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"保存历史记录失败: {e}")


def get_history(mode):
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("SELECT * FROM query_history WHERE mode = ? ORDER BY timestamp DESC LIMIT 20", (mode,))
        rows = c.fetchall()
        conn.close()
        return rows
    except:
        return []


# ================= AI 调用函数 =================

def call_ollama(prompt, model="qwen2.5:7b"):
    url = "http://localhost:11434/api/generate"
    data = {
        "model": model,
        "prompt": prompt,
        "stream": False
    }
    try:
        response = requests.post(url, json=data)
        if response.status_code == 200:
            return response.json().get("response", "")
        return f"Error: Ollama API returned {response.status_code}"
    except Exception as e:
        return f"无法连接 Ollama (Error: {str(e)})"


def extract_entity(user_input):
    """
    利用 LLM 从自然语言中提取核心症状关键词
    """
    if len(user_input) < 3:
        return user_input

    prompt = f"""
    你是一个医疗实体提取助手。
    请从用户的句子中提取出最核心的一个【疾病名称】或【症状名称】。
    规则：仅输出关键词，不要标点。
    用户：感冒了怎么治 -> 输出：感冒
    用户：{user_input} -> 输出：
    """
    keyword = call_ollama(prompt).strip()
    keyword = re.sub(r'[^\w]', '', keyword)  # 去除标点
    print(f"用户输入: {user_input} -> 提取关键词: {keyword}")
    return keyword


# ================= 路由逻辑 =================

@app.route('/')
def index():
    return redirect(url_for('home'))


@app.route('/home')
def home():
    return render_template('home.html', active_page='home')


# --- 1. 问答模式 (修正了查询语句) ---
@app.route('/answer', methods=['GET', 'POST'])
def answer():
    graph_data = {}
    table_data = []
    search_keyword = ""

    if request.method == 'POST':
        raw_input = request.form.get('symptom', '').strip()
        mode = "answer"

        if raw_input:
            search_keyword = extract_entity(raw_input)

            # 🚨 修正点：关系名改为 TREATS, COMPOSED_OF；属性改为 dosage
            # 🚨 修正点：功效现在是节点，通过 HAS_EFFICACY 关系查询
            cypher_query = """
            MATCH (p:Prescription)-[:TREATS]->(d:Disease)
            WHERE d.name CONTAINS $symptom
            MATCH (p)-[r:COMPOSED_OF]->(h:Herb)

            // 尝试获取该中药的功效（只取前3个拼接，避免太长）
            OPTIONAL MATCH (h)-[:HAS_EFFICACY]->(e:Efficacy)
            WITH p, d, h, r, collect(e.name)[0..3] as effs

            RETURN p.name as prescription, 
                   d.name as disease, 
                   h.name as herb, 
                   r.dosage as dose,
                   effs as efficacy
            LIMIT 50
            """

            with driver.session() as session:
                result = session.run(cypher_query, symptom=search_keyword)
                records = list(result)

            if records:
                nodes = []
                links = []
                categories = [{"name": "方剂"}, {"name": "功能主治"}, {"name": "中药"}]
                node_ids = set()

                for record in records:
                    pres = record["prescription"]
                    herb = record["herb"]
                    # disease = record["disease"] # 如果需要显示疾病节点可解开注释

                    # 1. 方剂节点
                    if pres not in node_ids:
                        nodes.append({
                            "id": pres,
                            "name": pres,
                            "category": 0,
                            "symbolSize": 45,
                            "itemStyle": {"color": COLOR_MAP["Prescription"]}
                        })
                        node_ids.add(pres)

                    # 2. 中药节点
                    if herb not in node_ids:
                        nodes.append({
                            "id": herb,
                            "name": herb,
                            "category": 2,
                            "symbolSize": 30,
                            "itemStyle": {"color": COLOR_MAP["Herb"]}
                        })
                        node_ids.add(herb)

                    # 3. 连线
                    links.append({"source": pres, "target": herb, "name": "组成"})

                    # 4. 表格数据
                    eff_str = "、".join(record["efficacy"]) if record["efficacy"] else "暂无"
                    table_data.append({
                        "方名": pres,
                        "中药": herb,
                        "剂量": record["dose"] if record["dose"] else "适量",
                        "中药功能主治": eff_str
                    })

                graph_data = {
                    "nodes": nodes,
                    "links": links,
                    "categories": categories
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
    if not symptom: return jsonify({"result": "请输入症状"})

    prompt = f"""
    你是一位资深中医专家。患者问题：“{symptom}”。
    请根据中医理论：
    1. 分析病因。
    2. 推荐经典方剂。
    3. 给出生活建议。
    回答亲切专业。
    """
    answer = call_ollama(prompt)
    return jsonify({"result": answer})


# --- 2. 推理模式 (修正了查询语句) ---
@app.route('/inference', methods=['GET', 'POST'])
def inference():
    graph_data = {}
    if request.method == 'POST':
        query = request.form.get('symptom', '')
        mode = "inference"

        names = re.split(r'\s+|和|与', query)
        names = [n for n in names if n]

        if len(names) >= 2:
            name1, name2 = names[0], names[1]
            # 🚨 修正点：[:composition] -> [:COMPOSED_OF]
            cypher = """
            MATCH (p:Prescription)
            WHERE p.name = $name1 OR p.name = $name2
            OPTIONAL MATCH (p)-[r:COMPOSED_OF]->(h:Herb)
            RETURN p.name as pres, h.name as herb, r.dosage as dosage
            """
            with driver.session() as session:
                result = session.run(cypher, name1=name1, name2=name2)
                records = list(result)

            if records:
                nodes = []
                links = []
                categories = [{"name": "方剂"}, {"name": "中药"}]
                node_ids = set()

                for rec in records:
                    p_name = rec['pres']
                    h_name = rec['herb']
                    dosage = rec['dosage'] if rec['dosage'] else ""

                    if p_name not in node_ids:
                        nodes.append({"id": p_name, "name": p_name, "category": 0, "symbolSize": 50,
                                      "itemStyle": {"color": COLOR_MAP["Prescription"]}})
                        node_ids.add(p_name)

                    if h_name:
                        if h_name not in node_ids:
                            nodes.append({"id": h_name, "name": h_name, "category": 1, "symbolSize": 30,
                                          "itemStyle": {"color": COLOR_MAP["Herb"]}})
                            node_ids.add(h_name)
                        # 线上的文字显示剂量
                        links.append({"source": p_name, "target": h_name, "name": dosage})

                graph_data = {"nodes": nodes, "links": links, "categories": categories}
                save_history(query, mode, True)
            else:
                save_history(query, mode, False)
        else:
            save_history(query, mode, False)

    return render_template('inference.html', graph_data=graph_data, active_page='inference')


@app.route('/suggest', methods=['POST'])
def suggest():
    symptom = request.form.get('symptom')
    prompt = f"请详细分析中药方剂：{symptom} 的异同点、配伍特点及临床应用区别。请用Markdown格式输出。"
    reply = call_ollama(prompt)
    return markdown.markdown(reply)


# --- 3. 药材搜索 (全量属性修正) ---
@app.route('/herb_search', methods=['GET', 'POST'])
def herb_search():
    graph_data = {}
    herb_info = {}
    if request.method == 'POST':
        herb_name = request.form.get('herb_name', '').strip()
        mode = "herb"
        if herb_name:
            cypher = """
            MATCH (h:Herb {name: $name})
            OPTIONAL MATCH (h)-[r]->(n)
            RETURN h, r, n, type(r) as rel_type, labels(n) as lbls
            LIMIT 100
            """
            with driver.session() as session:
                result = session.run(cypher, name=herb_name)
                records = list(result)

            if records:
                main_node = records[0]['h']
                herb_info = dict(main_node)
                nodes = [{"id": herb_name, "name": herb_name, "category": "Herb", "symbolSize": 60,
                          "itemStyle": {"color": COLOR_MAP["Herb"]}}]
                links = []
                # 动态收集类别
                cat_set = set(["Herb"])
                node_ids = {herb_name}

                for rec in records:
                    target = rec['n']
                    if target:
                        t_name = target.get('name', '未命名')
                        # 获取目标节点类型 (例如 Efficacy, Origin)
                        lbls = rec['lbls']
                        label = lbls[0] if lbls else "Other"
                        cat_set.add(label)

                        if t_name not in node_ids:
                            # 动态配色
                            color = COLOR_MAP.get(label, "#cccccc")

                            nodes.append({
                                "id": t_name,
                                "name": t_name,
                                "category": label,
                                "symbolSize": 30,
                                "itemStyle": {"color": color}
                            })
                            node_ids.add(t_name)

                        # 翻译关系名称用于显示
                        rel_type = rec['rel_type']
                        rel_display = rel_type
                        if rel_type == "HAS_EFFICACY": rel_display = "功效"
                        if rel_type == "PRODUCED_IN": rel_display = "产地"
                        if rel_type == "HAS_CONTRAINDICATION": rel_display = "禁忌"
                        if rel_type == "COMPOSED_OF": rel_display = "组成"
                        if rel_type == "TREATS": rel_display = "治疗"

                        links.append({"source": herb_name, "target": t_name, "name": rel_display})

                categories = [{"name": c} for c in list(cat_set)]
                graph_data = {"nodes": nodes, "links": links, "categories": categories}
                save_history(herb_name, mode, True)
            else:
                save_history(herb_name, mode, False)
    return render_template('herb_search.html', graph_data=graph_data, herb_info=herb_info, active_page='herb')


# --- 4. 数据大屏 & 词云 ---
@app.route('/dashboard')
def dashboard():
    with driver.session() as session:
        # 修正：标签名要对应英文
        total_herbs = session.run("MATCH (n:Herb) RETURN count(n) as c").single()['c']
        total_prescriptions = session.run("MATCH (n:Prescription) RETURN count(n) as c").single()['c']
        total_diseases = session.run("MATCH (n:Disease) RETURN count(n) as c").single()['c']
        total_relations = session.run("MATCH ()-[r]->() RETURN count(r) as c").single()['c']

        # 修正：HAS_EFFICACY
        eff_result = session.run("""
            MATCH (e:Efficacy)<-[:HAS_EFFICACY]-(h:Herb) 
            RETURN e.name as name, count(h) as value 
            ORDER BY value DESC LIMIT 10
        """)
        efficacy_data = [{"name": r['name'], "value": r['value']} for r in eff_result]

        # 修正：COMPOSED_OF
        top_herbs_res = session.run("""
            MATCH (p:Prescription)-[:COMPOSED_OF]->(h:Herb) 
            RETURN h.name as name, count(p) as count 
            ORDER BY count DESC LIMIT 10
        """)
        top_herbs_names = [r['name'] for r in top_herbs_res]
        top_herbs_counts = [r['count'] for r in top_herbs_res]

    return render_template('dashboard.html', total_herbs=total_herbs, total_prescriptions=total_prescriptions,
                           total_diseases=total_diseases, total_relations=total_relations,
                           efficacy_data=efficacy_data, top_herbs_names=top_herbs_names,
                           top_herbs_counts=top_herbs_counts, active_page='dashboard')


@app.route('/wordcloud')
def wordcloud():
    return render_template('wordcloud.html', active_page='wordcloud')


@app.route('/api/wordcloud_data')
def wordcloud_data():
    try:
        with driver.session() as session:
            # 修正：HAS_EFFICACY
            result = session.run(
                "MATCH (h:Herb)-[:HAS_EFFICACY]->(e:Efficacy) RETURN e.name AS name, COUNT(h) AS value ORDER BY value DESC LIMIT 100")
            return jsonify([{"name": r["name"], "value": r["value"]} for r in result])
    except Exception as e:
        return jsonify({"error": str(e)})


# --- 5. 历史记录 ---
@app.route('/history')
def history_page():
    return render_template('history.html', inference_history=get_history('inference'),
                           answer_history=get_history('answer'), herb_history=get_history('herb'),
                           active_page='history')


@app.route('/clear_history', methods=['POST'])
def clear_history_route():
    mode = request.form.get('mode')
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('DELETE FROM query_history WHERE mode = ?', (mode,))
        conn.commit()
        conn.close()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


if __name__ == '__main__':
    app.run(debug=True, port=5000)