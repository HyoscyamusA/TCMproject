from flask import Flask, request, render_template, jsonify, redirect, url_for
from neo4j import GraphDatabase
import os
import markdown
import sqlite3
import requests
import re
import json

app = Flask(__name__)

# ================= 配置区 =================
NEO4J_URI = "bolt://localhost:7687"
NEO4J_AUTH = ("neo4j", "12345678")
DB_PATH = os.path.join(os.path.dirname(__file__), 'TCM.db')

# 🎨 统一颜色配置 (Legend 和 Node 必须用同一套颜色)
COLOR_MAP = {
    "Prescription": "#87EBC1",  # 绿
    "Disease": "#EE90A1",  # 粉
    "Symptom": "#FF9F40",  # 橙
    "Herb": "#69B2FF",  # 蓝
    "Source": "#9A66E4",  # 紫
    "Category": "#FFC0CB",  # 浅粉
    "Efficacy": "#16C2D5",  # 青
    "Origin": "#B37FEB",  # 深紫
    # 兼容
    "方剂": "#87EBC1", "中药": "#69B2FF", "来源": "#9A66E4"
}

driver = GraphDatabase.driver(NEO4J_URI, auth=NEO4J_AUTH)

#
# def init_db():
#     conn = sqlite3.connect(DB_PATH)
#     conn.execute('''CREATE TABLE IF NOT EXISTS query_history
#                  (id INTEGER PRIMARY KEY AUTOINCREMENT, query TEXT, mode TEXT,
#                   timestamp DATETIME DEFAULT CURRENT_TIMESTAMP, has_result BOOLEAN)''')
#     conn.commit()
#     conn.close()
#
#
# init_db()


# def save_history(query, mode, has_result=True):
#     try:
#         conn = sqlite3.connect(DB_PATH)
#         conn.execute("INSERT INTO query_history (query, mode, has_result) VALUES (?, ?, ?)", (query, mode, has_result))
#         conn.commit()
#         conn.close()
#     except:
#         pass
#
#
# def get_history(mode):
#     try:
#         conn = sqlite3.connect(DB_PATH)
#         conn.row_factory = sqlite3.Row
#         c = conn.cursor()
#         c.execute("SELECT * FROM query_history WHERE mode = ? ORDER BY timestamp DESC LIMIT 20", (mode,))
#         return c.fetchall()
#     except:
#         return []
def init_db():
    """初始化 SQLite 数据库"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS query_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            query TEXT NOT NULL,
            mode TEXT NOT NULL,
            has_result BOOLEAN NOT NULL,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()
    
def save_history(query, mode, has_result=True):
    """保存历史，去重处理：防止刷新页面时连续插入重复的最后一条记录"""
    if not query:
        return
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()

        # 查找当前模式下的最后一条记录
        c.execute('SELECT query FROM query_history WHERE mode = ? ORDER BY id DESC LIMIT 1', (mode,))
        last_record = c.fetchone()

        # 只有当新查询和最后一条记录不一样时，才存入数据库，防止污染历史列表
        if not last_record or last_record[0] != query:
            c.execute('INSERT INTO query_history (query, mode, has_result) VALUES (?, ?, ?)',
                      (query, mode, has_result))
            conn.commit()
    except Exception as e:
        print(f"保存历史记录出错: {e}")
    finally:
        conn.close()


def get_history(mode):
    """获取历史记录供前端展示"""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row  # 让结果可以像字典一样被访问
        c = conn.cursor()
        c.execute('SELECT query, has_result, timestamp FROM query_history WHERE mode = ? ORDER BY id DESC LIMIT 20',
                  (mode,))
        records = c.fetchall()
        return records
    except Exception as e:
        print(f"获取历史记录出错: {e}")
        return []
    finally:
        conn.close()

def call_ollama(prompt, model="qwen2.5:1.5b"):
    url = "http://localhost:11434/api/generate"
    try:
        response = requests.post(url, json={"model": model, "prompt": prompt, "stream": False}, timeout=30)
        if response.status_code == 200: return response.json().get("response", "")
        return f"AI 响应错误: {response.status_code}"
    except:
        return "无法连接本地 AI 服务"


def format_node(node, category_override=None):
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
        "attributes": dict(node)
    }


@app.route('/')
def index(): return redirect(url_for('home'))


# @app.route('/home')
# def home(): return render_template('home.html', active_page='home')

@app.route('/')
@app.route('/home')
def home():
    # 获取统计数据供首页大屏展示
    with driver.session() as session:
        # 统计中药、方剂、疾病的总数，以及总关系数
        c1 = session.run("MATCH (n:Herb) RETURN count(n) AS c").single()['c']
        c2 = session.run("MATCH (n:Prescription) RETURN count(n) AS c").single()['c']
        c3 = session.run("MATCH (n:Disease) RETURN count(n) AS c").single()['c']
        c4 = session.run("MATCH ()-[r]->() RETURN count(r) AS c").single()['c']

    return render_template('home.html',
                           active_page='home',
                           total_herbs=c1,
                           total_prescriptions=c2,
                           total_diseases=c3,
                           total_relations=c4)

# --- 问答模式 ---
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
            cypher_query = """
            MATCH (d:Disease) WHERE d.name CONTAINS $symptom
            MATCH (p:Prescription)-[:TREATS]->(d)
            MATCH (p)-[r_comp:COMPOSED_OF]->(h:Herb)
            OPTIONAL MATCH (p)-[:HAS_SOURCE]->(src)
            OPTIONAL MATCH (d)-[:HAS_SYMPTOM]->(sym:Symptom)
            RETURN p, d, h, r_comp, src, sym
            LIMIT 200
            """
            with driver.session() as session:
                records = list(session.run(cypher_query, symptom=search_keyword))

            if records:
                nodes_dict = {}
                links = []
                # 🔥 修复颜色不对应：使用字典记录 category -> color
                category_map = {}
                seen_table_rows = set()  # 🔥 修复表格重复

                for rec in records:
                    # 节点处理辅助
                    def process(n, cat_fix=None):
                        if not n: return None
                        fmt = format_node(n, cat_fix)
                        nodes_dict[fmt['id']] = fmt
                        # 记录分类颜色用于 Legend
                        category_map[fmt['category']] = fmt['itemStyle']['color']
                        return fmt['id']

                    p_id = process(rec['p'], "Prescription")
                    d_id = process(rec['d'], "Disease")
                    h_id = process(rec['h'], "Herb")
                    src_id = process(rec['src'], "Source")
                    sym_id = process(rec['sym'], "Symptom")

                    # 连线
                    links.append({"source": p_id, "target": d_id, "name": "治疗"})
                    links.append({"source": p_id, "target": h_id, "name": rec['r_comp'].get('dosage', '组成')})
                    if src_id: links.append({"source": p_id, "target": src_id, "name": "出处"})
                    if sym_id: links.append({"source": d_id, "target": sym_id, "name": "症状"})

                    # 🔥 表格数据去重逻辑
                    p_name = rec['p'].get('name')
                    h_name = rec['h'].get('name')
                    unique_key = (p_name, h_name)  # 组合键

                    if unique_key not in seen_table_rows:
                        table_data.append({
                            "方名": p_name,
                            "出处": rec['src'].get('name', '未知') if rec['src'] else '未知',
                            "主治疾病": rec['d'].get('name'),
                            "中药": h_name,
                            "剂量": rec['r_comp'].get('dosage', '适量')
                        })
                        seen_table_rows.add(unique_key)

                # 🔥 构建带颜色的 categories
                categories = [{"name": name, "itemStyle": {"color": color}}
                              for name, color in category_map.items()]

                graph_data = {
                    "nodes": list(nodes_dict.values()),
                    "links": links,
                    "categories": categories
                }
                save_history(raw_input, mode, True)
            else:
                save_history(raw_input, mode, False)

    return render_template('answer.html', graph_data=graph_data, table_data=table_data, search_keyword=search_keyword,
                           active_page='answer')


@app.route('/ask_ollama', methods=['POST'])
def ask_ollama():
    symptom = request.form.get('symptom')
    return jsonify({"result": call_ollama(f"中医分析：{symptom}。分析病因并推荐方剂。")})


# --- 推理模式 ---
@app.route('/inference', methods=['GET', 'POST'])
def inference():
    graph_data = {}
    search_keyword = ""

    if request.method == 'POST':
        query = request.form.get('symptom', '').strip()
        search_keyword = query
        mode = "inference"
        names = re.split(r'\s+|和|与', query)
        names = [n for n in names if n]

        cypher = ""
        params = {}
        if len(names) == 1:
            cypher = "MATCH (p:Prescription {name: $name}) OPTIONAL MATCH (p)-[r]->(n) RETURN p, r, n LIMIT 200"
            params = {"name": names[0]}
        elif len(names) >= 2:
            cypher = "MATCH (p:Prescription) WHERE p.name IN [$n1, $n2] OPTIONAL MATCH (p)-[r]->(n) WHERE type(r) IN ['COMPOSED_OF', 'TREATS', 'HAS_SOURCE'] RETURN p, r, n LIMIT 200"
            params = {"n1": names[0], "n2": names[1]}

        if cypher:
            with driver.session() as session:
                records = list(session.run(cypher, **params))
            if records:
                nodes_dict = {}
                links = []
                category_map = {}

                for rec in records:
                    if not rec['p']: continue

                    p_fmt = format_node(rec['p'], "Prescription")
                    nodes_dict[p_fmt['id']] = p_fmt
                    category_map["Prescription"] = p_fmt['itemStyle']['color']

                    target = rec['n']
                    if target:
                        t_fmt = format_node(target)
                        nodes_dict[t_fmt['id']] = t_fmt
                        category_map[t_fmt['category']] = t_fmt['itemStyle']['color']

                        rel_show = rec['r'].type
                        if rel_show == "COMPOSED_OF": rel_show = rec['r'].get('dosage', '组成')

                        links.append({
                            "source": p_fmt['id'],
                            "target": t_fmt['id'],
                            "name": rel_show,
                            "attributes": dict(rec['r'])
                        })

                categories = [{"name": name, "itemStyle": {"color": color}}
                              for name, color in category_map.items()]

                graph_data = {"nodes": list(nodes_dict.values()), "links": links, "categories": categories}
                save_history(query, mode, True)
            else:
                save_history(query, mode, False)
        else:
            save_history(query, mode, False)

    return render_template('inference.html', graph_data=graph_data, search_keyword=search_keyword,
                           active_page='inference')


@app.route('/suggest', methods=['POST'])
def suggest():
    symptom = request.form.get('symptom')
    return markdown.markdown(call_ollama(f"中医分析方剂：{symptom}。"))


# ==========================================
# 替换 app.py 中的 herb_search 函数
# ==========================================

@app.route('/herb_search', methods=['GET', 'POST'])
def herb_search():
    # 初始化变量，防止模板渲染报错
    graph_data = None
    related_prescriptions = []
    search_term = ""

    if request.method == 'POST':
        search_term = request.form.get('herb_name', '').strip()

        if search_term:
            # 记录历史（如果有这个功能）
            # save_history('herb', search_term)

            # Cypher 查询：查找包含该药材的方剂，并提取边上的剂量(dosage)和炮制(processing)
            # 假设关系方向是：(方剂)-[r]->(药材)
            query = """
            MATCH (p:Prescription)-[r]->(h:Herb {name: $name})
            RETURN p.name AS p_name, 
                   r.dosage AS dosage, 
                   r.processing AS processing
            LIMIT 50
            """

            try:
                with driver.session() as session:
                    result = session.run(query, name=search_term).data()

                if result:
                    nodes = []
                    links = []
                    # 避免重复节点的集合
                    node_names = set()

                    # 1. 添加中心节点（查询的药材）
                    nodes.append({
                        "name": search_term,
                        "category": 0,
                        "symbolSize": 50,
                        "itemStyle": {"color": "#e74c3c"},  # 红色
                        "label": {"show": True}
                    })
                    node_names.add(search_term)

                    # 2. 遍历结果构建图谱
                    for record in result:
                        p_name = record.get('p_name', '未知方剂')
                        dosage = record.get('dosage', '')
                        processing = record.get('processing', '')

                        # 添加方剂节点（如果还没添加过）
                        if p_name not in node_names:
                            nodes.append({
                                "name": p_name,
                                "category": 1,
                                "symbolSize": 30,
                                "itemStyle": {"color": "#3498db"},  # 蓝色
                                "label": {"show": True}
                            })
                            node_names.add(p_name)

                        # 添加连线
                        links.append({
                            "source": p_name,
                            "target": search_term,
                            "dosage": dosage,  # 传给前端用于显示
                            "processing": processing
                        })

                        # 添加到右侧列表数据
                        related_prescriptions.append({
                            "name": p_name,
                            "dosage": dosage,
                            "processing": processing
                        })

                    # 封装成 ECharts 数据结构
                    graph_data = {
                        "nodes": nodes,
                        "links": links,
                        "categories": [{"name": "查询药材"}, {"name": "关联方剂"}]
                    }

            except Exception as e:
                print(f"Neo4j查询错误: {e}")

    # 渲染模板，注意 active_page 用于导航栏高亮
    return render_template('herb_search.html',
                           active_page='herb_search',
                           graph_data=graph_data,
                           related_prescriptions=related_prescriptions,
                           search_term=search_term)

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
                           total_relations=c['c4'], efficacy_data=[{"name": r['n'], "value": r['c']} for r in eff],
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
def history_page(): return render_template('history.html', inference_history=get_history('inference'),
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
    except:
        return jsonify({'success': False})


if __name__ == '__main__':
    app.run(debug=True, port=5000)