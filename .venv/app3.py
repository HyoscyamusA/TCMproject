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
NEO4J_AUTH = ("neo4j", "12345678")  # ⚠️ 确认密码
DB_PATH = os.path.join(os.path.dirname(__file__), 'TCM.db')

# 颜色配置
COLOR_MAP = {
    "Prescription": "#87EBC1",
    "Disease": "#EE90A1",
    "Herb": "#69B2FF",
    "Category": "#FF9F40",
    "Efficacy": "#16C2D5",
    "Literature": "#9A66E4",
    # 兼容
    "方剂": "#87EBC1",
    "中药": "#69B2FF"
}

driver = GraphDatabase.driver(NEO4J_URI, auth=NEO4J_AUTH)


# ================= 数据库初始化 =================
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
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
        print(f"历史记录保存失败: {e}")


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


# ================= AI 模块修复 =================
def call_ollama(prompt, model="qwen2.5:1.5b"):
    # ⚠️ 确保你已经运行了: ollama pull qwen2.5:7b
    url = "http://localhost:11434/api/generate"

    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False
    }

    try:
        response = requests.post(url, json=payload, timeout=30)
        if response.status_code == 200:
            res_json = response.json()
            return res_json.get("response", "")
        elif response.status_code == 404:
            return "错误 404: Ollama 服务未找到或模型未下载。请在终端运行: qwen2.5:1.5b"
        else:
            return f"Ollama API 错误: {response.status_code}"
    except requests.exceptions.ConnectionError:
        return "连接失败: 请确保 Ollama 服务已启动 (在终端输入 'ollama serve')"
    except Exception as e:
        return f"AI 调用出错: {str(e)}"


def extract_entity(user_input):
    if len(user_input) < 2: return user_input
    # 简单规则提取，避免每次都调 AI 变慢
    # 如果需要 AI 提取，可恢复调用 call_ollama
    return user_input


# ================= 路由逻辑 =================

@app.route('/')
def index(): return redirect(url_for('home'))


@app.route('/home')
def home(): return render_template('home.html', active_page='home')


# --- 问答模式 (修复详情显示和表格) ---
@app.route('/answer', methods=['GET', 'POST'])
def answer():
    graph_data = {}
    table_data = []
    search_keyword = ""

    if request.method == 'POST':
        raw_input = request.form.get('symptom', '').strip()
        search_keyword = raw_input
        mode = "answer"

        # 1. 核心查询
        # ⚠️ 增加了 ORDER BY p.name，这对表格合并单元格至关重要！
        cypher_query = """
        MATCH (p:Prescription)-[:TREATS]->(d:Disease)
        WHERE d.name CONTAINS $symptom
        MATCH (p)-[r:COMPOSED_OF]->(h:Herb)

        // 获取方剂的来源（如果有）
        OPTIONAL MATCH (p)-[:SOURCE_IS]->(s:Source)

        // 获取中药的功效
        OPTIONAL MATCH (h)-[:HAS_EFFICACY]->(e:Efficacy)

        WITH p, d, h, r, s, collect(e.name) as effs
        ORDER BY p.name 
        LIMIT 100

        RETURN p.name as pres_name, 
               labels(p) as p_labels,
               s.name as source,
               d.name as disease,
               h.name as herb_name,
               r.dosage as dosage,
               effs as efficacy
        """

        with driver.session() as session:
            result = session.run(cypher_query, symptom=search_keyword)
            records = list(result)

        if records:
            nodes = []
            links = []
            categories = [{"name": "方剂"}, {"name": "中药"}]
            node_ids = set()

            for rec in records:
                p_name = rec["pres_name"]
                h_name = rec["herb_name"]
                dosage = rec["dosage"] if rec["dosage"] else "适量"
                eff_str = "、".join(rec["efficacy"][:5]) if rec["efficacy"] else "暂无数据"
                source = rec["source"] if rec["source"] else "未知"

                # --- 构建方剂节点 (带详细属性) ---
                if p_name not in node_ids:
                    nodes.append({
                        "id": p_name,
                        "name": p_name,
                        "category": 0,
                        "symbolSize": 50,
                        "itemStyle": {"color": COLOR_MAP["Prescription"]},
                        # ✨ 这里是关键：把详情放入 attributes 字段
                        "attributes": {
                            "类型": "方剂",
                            "出处": source,
                            "主治": rec["disease"]
                        }
                    })
                    node_ids.add(p_name)

                # --- 构建中药节点 (带详细属性) ---
                if h_name not in node_ids:
                    nodes.append({
                        "id": h_name,
                        "name": h_name,
                        "category": 1,
                        "symbolSize": 35,
                        "itemStyle": {"color": COLOR_MAP["Herb"]},
                        # ✨ 中药详情
                        "attributes": {
                            "类型": "中药",
                            "主要功效": eff_str
                        }
                    })
                    node_ids.add(h_name)

                # --- 构建连线 (带属性) ---
                links.append({
                    "source": p_name,
                    "target": h_name,
                    "name": dosage,  # 这一行让线上的字显示剂量
                    "attributes": {
                        "关系": "组成",
                        "剂量": dosage
                    }
                })

                # --- 表格数据 ---
                table_data.append({
                    "方名": p_name,
                    "出处": source,
                    "中药": h_name,
                    "剂量": dosage,
                    "功效": eff_str
                })

            graph_data = {"nodes": nodes, "links": links, "categories": categories}
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
    if not symptom: return jsonify({"result": "请输入内容"})

    prompt = f"""
    你是一位资深中医。用户提到症状：“{symptom}”。
    请简要分析可能的原因，并推荐1-2个经典方剂（仅供参考）。
    请用纯文本回答，不要使用Markdown格式。
    """
    answer = call_ollama(prompt)
    return jsonify({"result": answer})


# --- 推理模式 ---
@app.route('/inference', methods=['GET', 'POST'])
def inference():
    graph_data = {}
    if request.method == 'POST':
        query = request.form.get('symptom', '')
        mode = "inference"
        names = re.split(r'\s+|和|与', query)
        names = [n for n in names if n]

        if len(names) >= 2:
            n1, n2 = names[0], names[1]
            cypher = """
            MATCH (p:Prescription) WHERE p.name IN [$n1, $n2]
            MATCH (p)-[r:COMPOSED_OF]->(h:Herb)
            RETURN p.name as pres, h.name as herb, r.dosage as dosage
            """
            with driver.session() as session:
                records = list(session.run(cypher, n1=n1, n2=n2))

            if records:
                nodes = []
                links = []
                ids = set()
                categories = [{"name": "方剂"}, {"name": "中药"}]

                for r in records:
                    p, h, d = r['pres'], r['herb'], r['dosage']
                    if p not in ids:
                        nodes.append({"id": p, "name": p, "category": 0, "symbolSize": 50,
                                      "itemStyle": {"color": COLOR_MAP["Prescription"]},
                                      "attributes": {"类型": "方剂"}})
                        ids.add(p)
                    if h not in ids:
                        nodes.append({"id": h, "name": h, "category": 1, "symbolSize": 30,
                                      "itemStyle": {"color": COLOR_MAP["Herb"]},
                                      "attributes": {"类型": "中药"}})
                        ids.add(h)
                    links.append({"source": p, "target": h, "name": d, "attributes": {"剂量": d}})

                graph_data = {"nodes": nodes, "links": links, "categories": categories}
                save_history(query, mode, True)
            else:
                save_history(query, mode, False)

    return render_template('inference.html', graph_data=graph_data, active_page='inference')


@app.route('/suggest', methods=['POST'])
def suggest():
    symptom = request.form.get('symptom')
    prompt = f"分析中药方剂：{symptom} 的异同及配伍特点。"
    return markdown.markdown(call_ollama(prompt))


# --- 药材搜索 (全量修复) ---
@app.route('/herb_search', methods=['GET', 'POST'])
def herb_search():
    graph_data = {}
    herb_info = {}
    if request.method == 'POST':
        name = request.form.get('herb_name', '').strip()
        mode = "herb"

        # 搜索该药材及其直接关系
        cypher = """
        MATCH (h:Herb {name: $name})
        OPTIONAL MATCH (h)-[r]->(n)
        RETURN h, type(r) as rel, n, labels(n) as lbls
        LIMIT 50
        """
        with driver.session() as session:
            records = list(session.run(cypher, name=name))

        if records:
            main = records[0]['h']
            herb_info = dict(main)  # 转字典
            nodes = [{"id": name, "name": name, "category": "Herb", "symbolSize": 60,
                      "itemStyle": {"color": COLOR_MAP["Herb"]},
                      "attributes": herb_info}]  # 将自身属性放入attributes
            links = []
            ids = {name}
            cats = set(["Herb"])

            for rec in records:
                target = rec['n']
                rel_type = rec['rel']
                if target:
                    t_name = target.get('name', '未知')
                    lbl = rec['lbls'][0] if rec['lbls'] else "Other"
                    cats.add(lbl)

                    if t_name not in ids:
                        nodes.append({
                            "id": t_name, "name": t_name, "category": lbl, "symbolSize": 30,
                            "itemStyle": {"color": COLOR_MAP.get(lbl, "#ccc")},
                            "attributes": {"类型": lbl, "名称": t_name}
                        })
                        ids.add(t_name)

                    # 翻译关系
                    rel_map = {"HAS_EFFICACY": "功效", "PRODUCED_IN": "产地",
                               "HAS_CONTRAINDICATION": "禁忌", "COMPOSED_OF": "组成"}
                    display_rel = rel_map.get(rel_type, rel_type)

                    links.append({"source": name, "target": t_name, "name": display_rel,
                                  "attributes": {"关系类型": display_rel}})

            graph_data = {"nodes": nodes, "links": links,
                          "categories": [{"name": c} for c in list(cats)]}
            save_history(name, mode, True)
        else:
            save_history(name, mode, False)

    return render_template('herb_search.html', graph_data=graph_data, herb_info=herb_info, active_page='herb')


# --- Dashboard & Wordcloud (保持原逻辑但修复Query) ---
@app.route('/dashboard')
def dashboard():
    with driver.session() as session:
        counts = session.run("""
            MATCH (h:Herb) WITH count(h) as c1
            MATCH (p:Prescription) WITH c1, count(p) as c2
            MATCH (d:Disease) WITH c1, c2, count(d) as c3
            MATCH ()-[r]->() RETURN c1, c2, c3, count(r) as c4
        """).single()

        eff_res = session.run(
            "MATCH (h:Herb)-[:HAS_EFFICACY]->(e:Efficacy) RETURN e.name as n, count(h) as c ORDER BY c DESC LIMIT 10")
        eff_data = [{"name": r['n'], "value": r['c']} for r in eff_res]

        herb_res = session.run(
            "MATCH (p:Prescription)-[:COMPOSED_OF]->(h:Herb) RETURN h.name as n, count(p) as c ORDER BY c DESC LIMIT 10")
        herb_names = [r['n'] for r in herb_res]
        herb_vals = [r['c'] for r in herb_res]

    return render_template('dashboard.html', total_herbs=counts['c1'], total_prescriptions=counts['c2'],
                           total_diseases=counts['c3'], total_relations=counts['c4'],
                           efficacy_data=eff_data, top_herbs_names=herb_names, top_herbs_counts=herb_vals,
                           active_page='dashboard')


@app.route('/wordcloud')
def wordcloud(): return render_template('wordcloud.html', active_page='wordcloud')


@app.route('/api/wordcloud_data')
def wordcloud_data():
    with driver.session() as session:
        res = session.run(
            "MATCH (h:Herb)-[:HAS_EFFICACY]->(e:Efficacy) RETURN e.name as n, count(h) as c ORDER BY c DESC LIMIT 80")
        return jsonify([{"name": r['n'], "value": r['c']} for r in res])


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