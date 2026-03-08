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
# ⚠️ 请务必确认密码是否正确
NEO4J_URI = "bolt://localhost:7687"
NEO4J_AUTH = ("neo4j", "12345678")

# SQLite 历史记录数据库路径
DB_PATH = os.path.join(os.path.dirname(__file__), 'TCM.db')

# 颜色配置表 (对应 ECharts 节点颜色)
COLOR_MAP = {
    # 标准英文 Label
    "Prescription": "#87EBC1",  # 方剂 (绿)
    "Disease": "#EE90A1",  # 疾病 (粉)
    "Symptom": "#FF9F40",  # 症状 (橙)
    "Herb": "#69B2FF",  # 中药 (蓝)
    "Source": "#9A66E4",  # 出处 (紫)
    "Literature": "#9A66E4",  # 文献 (紫)
    "Category": "#FFC0CB",  # 分类 (浅粉)
    "Efficacy": "#16C2D5",  # 功效 (青)
    "Origin": "#B37FEB",  # 产地 (深紫)
    "Contraindication": "#FF4D4F",  # 禁忌 (红)

    # 兼容可能存在的中文 Label
    "方剂": "#87EBC1",
    "中药": "#69B2FF",
    "功能主治": "#EE90A1"
}

# 初始化 Neo4j 驱动
driver = GraphDatabase.driver(NEO4J_URI, auth=NEO4J_AUTH)


# ================= 2. 数据库辅助函数 =================

def init_db():
    """初始化 SQLite 历史记录表"""
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


# 启动时初始化
init_db()


def save_history(query, mode, has_result=True):
    """保存查询历史"""
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
    """获取历史记录"""
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


# ================= 3. 通用工具函数 =================

def call_ollama(prompt, model="qwen2.5:1.5b"):
    """
    调用本地 Ollama AI 模型
    如果不确定模型名字，请在终端输入 `ollama list` 查看
    """
    url = "http://localhost:11434/api/generate"
    data = {
        "model": model,
        "prompt": prompt,
        "stream": False
    }
    try:
        response = requests.post(url, json=data, timeout=30)
        if response.status_code == 200:
            return response.json().get("response", "")
        elif response.status_code == 404:
            return f"错误: 本地未找到模型 '{model}'，请检查名称或运行 'ollama pull {model}'"
        else:
            return f"Ollama API 返回错误: {response.status_code}"
    except requests.exceptions.ConnectionError:
        return "无法连接 Ollama，请确保服务已启动 (ollama serve)"
    except Exception as e:
        return f"AI 调用异常: {str(e)}"


def format_node(node, category_override=None):
    """
    将 Neo4j 节点对象格式化为 ECharts 节点格式
    自动提取所有属性放入 attributes 字段
    """
    labels = list(node.labels)
    # 确定类别：优先用 override，否则用第一个 Label，都没有则 Unknown
    category = category_override if category_override else (labels[0] if labels else "Unknown")

    # 确定显示名称：优先 name 属性，否则用 ID
    name = node.get('name', f"Node-{node.element_id}")

    # 确定颜色
    color = COLOR_MAP.get(category, "#cccccc")

    # 确定大小权重
    size_map = {
        "Prescription": 50,
        "Herb": 35,
        "Disease": 40,
        "Symptom": 25,
        "Source": 30
    }
    size = size_map.get(category, 30)

    return {
        "id": str(node.element_id),  # 使用 Neo4j 内部 ID 避免重名冲突
        "name": name,
        "category": category,
        "symbolSize": size,
        "itemStyle": {"color": color},
        # 🔥 关键：将节点的所有属性转为字典，传给前端展示
        "attributes": dict(node)
    }


# ================= 4. 路由逻辑 =================

@app.route('/')
def index():
    return redirect(url_for('home'))


@app.route('/home')
def home():
    return render_template('home.html', active_page='home')


# --- 功能 1：问答模式 (增强版) ---
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
            # Cypher 查询逻辑：
            # 1. 查找 方剂(Prescription) 治疗(TREATS) 疾病(Disease)
            #    支持通过“症状名”查疾病，或者直接查“方剂名”
            # 2. 同时查找 方剂的组成(COMPOSED_OF) -> 中药
            # 3. 查找 方剂的出处(HAS_SOURCE) -> 出处
            # 4. 查找 疾病关联的症状(HAS_SYMPTOM) -> 症状

            cypher_query = """
            MATCH (p:Prescription)-[:TREATS]->(d:Disease)
            WHERE d.name CONTAINS $symptom OR p.name CONTAINS $symptom

            // 查组成
            MATCH (p)-[r_comp:COMPOSED_OF]->(h:Herb)

            // 查出处 (可选)
            OPTIONAL MATCH (p)-[:HAS_SOURCE]->(src)

            // 查关联症状 (可选，限制数量)
            OPTIONAL MATCH (d)-[:HAS_SYMPTOM]->(sym:Symptom)

            RETURN p, d, h, r_comp, src, sym
            LIMIT 150
            """

            with driver.session() as session:
                result = session.run(cypher_query, symptom=search_keyword)
                records = list(result)

            if records:
                nodes_dict = {}  # 用字典去重，key为节点ID
                links = []
                categories = set()

                # 辅助函数：添加节点并记录类别
                def add_node_to_dict(n, cat_override=None):
                    if n:
                        fmt_n = format_node(n, cat_override)
                        nodes_dict[fmt_n['id']] = fmt_n
                        categories.add(fmt_n['category'])
                        return fmt_n['id']
                    return None

                for rec in records:
                    p_id = add_node_to_dict(rec['p'], "Prescription")
                    d_id = add_node_to_dict(rec['d'], "Disease")
                    h_id = add_node_to_dict(rec['h'], "Herb")
                    src_id = add_node_to_dict(rec['src'], "Source")
                    sym_id = add_node_to_dict(rec['sym'], "Symptom")

                    r_comp = rec['r_comp']
                    dosage = r_comp.get('dosage', '适量')

                    # 建立连线 (注意去重逻辑通常由前端ECharts处理，或在此使用Set处理，这里简单添加)

                    # 方剂 -> 疾病
                    links.append({"source": p_id, "target": d_id, "name": "治疗"})

                    # 方剂 -> 中药 (带属性)
                    links.append({
                        "source": p_id,
                        "target": h_id,
                        "name": dosage,  # 线上的文字
                        "attributes": dict(r_comp)  # 关系的属性
                    })

                    # 方剂 -> 出处
                    if src_id:
                        links.append({"source": p_id, "target": src_id, "name": "出处"})

                    # 疾病 -> 症状
                    if sym_id:
                        links.append({"source": d_id, "target": sym_id, "name": "症状"})

                    # 填充表格数据
                    p_name = rec['p'].get('name')
                    src_name = rec['src'].get('name', '未知') if rec['src'] else '未知'

                    table_data.append({
                        "方名": p_name,
                        "出处": src_name,
                        "中药": rec['h'].get('name'),
                        "剂量": dosage,
                        "主治疾病": rec['d'].get('name')
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
    """问答模式的 AI 接口"""
    symptom = request.form.get('symptom')
    if not symptom:
        return jsonify({"result": "请输入查询内容"})

    prompt = f"""
    你是一位资深中医专家。用户查询的内容是：“{symptom}”。
    请结合中医理论：
    1. 如果是症状，分析病因病机。
    2. 如果是方剂，简述其功用。
    3. 给出1条生活调理建议。
    请保持回答简洁、专业，纯文本输出。
    """
    answer = call_ollama(prompt)
    return jsonify({"result": answer})


# --- 功能 2：推理模式 (支持单方剂查询 + 双方剂对比) ---
@app.route('/inference', methods=['GET', 'POST'])
def inference():
    graph_data = {}

    if request.method == 'POST':
        query = request.form.get('symptom', '').strip()
        mode = "inference"

        # 拆分输入，支持 "方剂A" 或 "方剂A 方剂B" (空格、和、与 分隔)
        names = re.split(r'\s+|和|与', query)
        names = [n for n in names if n]

        cypher = ""
        params = {}

        if len(names) == 1:
            # === 单方剂模式：查全家桶 (所有一度关系) ===
            cypher = """
            MATCH (p:Prescription {name: $name})
            OPTIONAL MATCH (p)-[r]->(n)
            RETURN p, r, n
            LIMIT 100
            """
            params = {"name": names[0]}

        elif len(names) >= 2:
            # === 双方剂对比模式：查共同组成 ===
            cypher = """
            MATCH (p:Prescription) WHERE p.name IN [$n1, $n2]
            OPTIONAL MATCH (p)-[r:COMPOSED_OF]->(h:Herb)
            RETURN p, r, h as n
            """
            params = {"n1": names[0], "n2": names[1]}

        if cypher:
            with driver.session() as session:
                result = session.run(cypher, **params)
                records = list(result)

            if records:
                nodes_dict = {}
                links = []
                categories = set()

                for rec in records:
                    if not rec['p']: continue

                    # 处理中心方剂节点
                    p_fmt = format_node(rec['p'], "Prescription")
                    nodes_dict[p_fmt['id']] = p_fmt
                    categories.add("Prescription")

                    target = rec.get('n')
                    rel = rec.get('r')

                    if target:
                        # 处理关联节点 (自动识别类别)
                        t_fmt = format_node(target)
                        nodes_dict[t_fmt['id']] = t_fmt
                        categories.add(t_fmt['category'])

                        # 处理关系显示名称
                        rel_type = rel.type
                        display_name = rel_type
                        if rel_type == "COMPOSED_OF":
                            display_name = rel.get('dosage', '组成')
                        elif rel_type == "TREATS":
                            display_name = "主治"
                        elif rel_type == "HAS_SOURCE":
                            display_name = "出处"
                        elif rel_type == "HAS_CATEGORY":
                            display_name = "分类"

                        links.append({
                            "source": str(rec['p'].element_id),
                            "target": str(target.element_id),
                            "name": display_name,
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

    return render_template('inference.html', graph_data=graph_data, active_page='inference')


@app.route('/suggest', methods=['POST'])
def suggest():
    """推理模式的 AI 建议"""
    symptom = request.form.get('symptom')
    prompt = f"请从中医角度分析：{symptom}。如果是单个方剂，介绍其功效和禁忌；如果是两个方剂，比较它们的异同点。"
    return markdown.markdown(call_ollama(prompt))


# --- 功能 3：药材搜索 (全属性展示) ---
@app.route('/herb_search', methods=['GET', 'POST'])
def herb_search():
    graph_data = {}
    herb_info = {}

    if request.method == 'POST':
        name = request.form.get('herb_name', '').strip()
        mode = "herb"

        if name:
            cypher = """
            MATCH (h:Herb {name: $name})
            OPTIONAL MATCH (h)-[r]->(n)
            RETURN h, type(r) as rel_type, n
            LIMIT 100
            """
            with driver.session() as session:
                records = list(session.run(cypher, name=name))

            if records:
                main_node = records[0]['h']
                herb_info = dict(main_node)  # 全量属性

                nodes_dict = {}
                links = []
                categories = set()

                # 添加中心节点
                h_fmt = format_node(main_node, "Herb")
                nodes_dict[h_fmt['id']] = h_fmt
                categories.add("Herb")

                for rec in records:
                    target = rec['n']
                    rel_type = rec['rel_type']

                    if target:
                        t_fmt = format_node(target)
                        nodes_dict[t_fmt['id']] = t_fmt
                        categories.add(t_fmt['category'])

                        # 关系名汉化
                        rel_map = {
                            "HAS_EFFICACY": "功效",
                            "PRODUCED_IN": "产地",
                            "HAS_CONTRAINDICATION": "禁忌",
                            "HAS_FLAVOR": "性味",
                            "HAS_MERIDIAN": "归经",
                            "CITED_BY": "文献"
                        }
                        display_rel = rel_map.get(rel_type, rel_type)

                        links.append({
                            "source": h_fmt['id'],
                            "target": t_fmt['id'],
                            "name": display_rel
                        })

                graph_data = {
                    "nodes": list(nodes_dict.values()),
                    "links": links,
                    "categories": [{"name": c} for c in list(categories)]
                }
                save_history(name, mode, True)
            else:
                save_history(name, mode, False)

    return render_template('herb_search.html',
                           graph_data=graph_data,
                           herb_info=herb_info,
                           active_page='herb')


# --- 功能 4：数据大屏 & 词云 ---

@app.route('/dashboard')
def dashboard():
    with driver.session() as session:
        # 统计节点总数
        counts = session.run("""
            MATCH (h:Herb) WITH count(h) as c1
            MATCH (p:Prescription) WITH c1, count(p) as c2
            MATCH (d:Disease) WITH c1, c2, count(d) as c3
            MATCH ()-[r]->() RETURN c1, c2, c3, count(r) as c4
        """).single()

        # 统计功效 TOP10
        eff_res = session.run("""
            MATCH (h:Herb)-[:HAS_EFFICACY]->(e:Efficacy) 
            RETURN e.name as n, count(h) as c 
            ORDER BY c DESC LIMIT 10
        """)
        eff_data = [{"name": r['n'], "value": r['c']} for r in eff_res]

        # 统计高频中药 TOP10
        herb_res = session.run("""
            MATCH (p:Prescription)-[:COMPOSED_OF]->(h:Herb) 
            RETURN h.name as n, count(p) as c 
            ORDER BY c DESC LIMIT 10
        """)
        herb_names = [r['n'] for r in herb_res]
        herb_vals = [r['c'] for r in herb_res]

    return render_template('dashboard.html',
                           total_herbs=counts['c1'],
                           total_prescriptions=counts['c2'],
                           total_diseases=counts['c3'],
                           total_relations=counts['c4'],
                           efficacy_data=eff_data,
                           top_herbs_names=herb_names,
                           top_herbs_counts=herb_vals,
                           active_page='dashboard'
                           )


@app.route('/wordcloud')
def wordcloud():
    """渲染词云页面"""
    return render_template('wordcloud.html', active_page='wordcloud')


@app.route('/api/wordcloud_data')
def wordcloud_data():
    """词云数据 JSON 接口"""
    try:
        with driver.session() as session:
            result = session.run("""
                MATCH (h:Herb)-[:HAS_EFFICACY]->(e:Efficacy)
                RETURN e.name AS name, COUNT(h) AS value
                ORDER BY value DESC
                LIMIT 100
            """)
            data = [{"name": r["name"], "value": r["value"]} for r in result]
            return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)})


# --- 功能 5：历史记录管理 ---

@app.route('/history')
def history_page():
    return render_template('history.html',
                           inference_history=get_history('inference'),
                           answer_history=get_history('answer'),
                           herb_history=get_history('herb'),
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
    # 启动 Flask 应用
    app.run(debug=True, port=5000)