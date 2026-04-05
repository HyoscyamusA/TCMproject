from flask import Flask, request, render_template, jsonify, redirect, url_for, session, flash
from neo4j import GraphDatabase
import os
import markdown
import sqlite3
import requests
import re
import json

app = Flask(__name__)
# 🌟 必须配置密钥，否则 session 和 flash 无法工作
app.secret_key = 'tcm_system_secret_key_2024'

# ================= 配置区 =================
NEO4J_URI = "bolt://localhost:7687"
NEO4J_AUTH = ("neo4j", "12345678")
DB_PATH = os.path.join(os.path.dirname(__file__), 'TCM.db')

COLOR_MAP = {
    "Prescription": "#87EBC1",
    "Disease": "#EE90A1",
    "Symptom": "#FF9F40",
    "Herb": "#69B2FF",
    "Source": "#9A66E4",
    "Category": "#FFC0CB",
    "Efficacy": "#16C2D5",
    "Origin": "#B37FEB",
    "Department": "#FFD700",  # 新增：科室设为金色
    "方剂": "#87EBC1", "中药": "#69B2FF", "来源": "#9A66E4"
}

driver = GraphDatabase.driver(NEO4J_URI, auth=NEO4J_AUTH)


# 🌟 全局登录校验拦截器
@app.before_request
def check_login():
    # 增加 'index' 到白名单中，允许未登录访问首页
    white_list = ['index', 'login', 'register', 'static']
    if 'user_id' not in session and request.endpoint not in white_list:
        return redirect(url_for('login'))


def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    # 查询历史表（增加 ai_response 列）
    c.execute('''CREATE TABLE IF NOT EXISTS query_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        query TEXT NOT NULL,
        mode TEXT NOT NULL,
        has_result BOOLEAN NOT NULL,
        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
    )''')
    c.execute("PRAGMA table_info(query_history)")
    if 'ai_response' not in [col[1] for col in c.fetchall()]:
        c.execute("ALTER TABLE query_history ADD COLUMN ai_response TEXT")

    # 用户表（直接包含 role 列）
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL,
        role TEXT DEFAULT 'user'
    )''')
    # 如果旧表缺少 role 列，则添加
    c.execute("PRAGMA table_info(users)")
    columns = [col[1] for col in c.fetchall()]
    if 'role' not in columns:
        c.execute("ALTER TABLE users ADD COLUMN role TEXT DEFAULT 'user'")

    # 智能问答对话历史表
    c.execute('''CREATE TABLE IF NOT EXISTS chat_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        question TEXT NOT NULL,
        answer TEXT NOT NULL,
        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(id)
    )''')
    conn.commit()
    conn.close()

init_db() # 启动时尝试初始化


def save_history(query, mode, has_result=True):
    if not query: return
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('SELECT query FROM query_history WHERE mode = ? ORDER BY id DESC LIMIT 1', (mode,))
        last_record = c.fetchone()
        if not last_record or last_record[0] != query:
            c.execute('INSERT INTO query_history (query, mode, has_result) VALUES (?, ?, ?)',
                      (query, mode, has_result))
            conn.commit()
    except Exception as e:
        print(f"保存历史记录出错: {e}")
    finally:
        conn.close()

def get_history(mode):
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute('SELECT query, has_result, timestamp FROM query_history WHERE mode = ? ORDER BY id DESC LIMIT 20',
                  (mode,))
        return c.fetchall()
    except:
        return []
    finally:
        conn.close()

def call_ollama(prompt, model="qwen2.5:7b"):
    url = "http://localhost:11434/api/generate"
    try:
        response = requests.post(url, json={"model": model, "prompt": prompt, "stream": False}, timeout=90)
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





# --- 核心页面路由 ---


@app.route('/graph_overview')
def graph_overview():
    # 确保用户已登录
    if 'user_id' not in session:
        return redirect(url_for('login'))

    # 🌟 新增：获取前端传来的 limit 参数，默认为 '200'
    limit = request.args.get('limit', '200')

    nodes_dict = {}
    links = []
    categories_dict = {}

    with driver.session() as neo4j_session:
        try:
            # 🌟 新增：根据参数决定查询语句
            if limit == 'all':
                query = "MATCH (n)-[r]->(m) RETURN n, r, m"
            else:
                # 防错处理，确保是数字
                limit_num = int(limit) if limit.isdigit() else 200
                query = f"MATCH (n)-[r]->(m) RETURN n, r, m LIMIT {limit_num}"

            results = neo4j_session.run(query)

            for record in results:
                n = record['n']
                m = record['m']
                r = record['r']

                # 处理节点 N
                n_id = str(n.element_id)
                n_label = list(n.labels)[0] if n.labels else 'Unknown'
                if n_id not in nodes_dict:
                    nodes_dict[n_id] = {
                        "id": n_id, "name": n.get("name", "未知"), "category": n_label,
                        "symbolSize": 45 if n_label == 'Prescription' else 30,
                        "attributes": dict(n)
                    }
                    categories_dict[n_label] = {"name": n_label}

                # 处理节点 M
                m_id = str(m.element_id)
                m_label = list(m.labels)[0] if m.labels else 'Unknown'
                if m_id not in nodes_dict:
                    nodes_dict[m_id] = {
                        "id": m_id, "name": m.get("name", "未知"), "category": m_label,
                        "symbolSize": 45 if m_label == 'Prescription' else 30,
                        "attributes": dict(m)
                    }
                    categories_dict[m_label] = {"name": m_label}

                # 处理关系
                links.append({
                    "source": n_id, "target": m_id, "name": type(r).__name__
                })

        except Exception as e:
            print(f"全局图谱查询失败: {e}")

    graph_data = {
        "nodes": list(nodes_dict.values()),
        "links": links,
        "categories": list(categories_dict.values())
    }

    # 🌟 把 current_limit 传给前端，用于高亮当前选中的按钮
    return render_template('graph_overview.html', active_page='overview',
                           graph_data=json.dumps(graph_data), current_limit=limit)



# ================= 新增：公开科普首页 =================
@app.route('/')
def index():
    # 如果用户已经登录，直接跳转到系统内部首页 (/home)
    if 'user_id' in session:
        return redirect(url_for('home'))
    # 如果未登录，则渲染刚才写好的公开科普页面
    return render_template('index.html')

# ================= 修改：系统内部大屏 =================
# 注意：这里删除了 @app.route('/')，只保留 /home
@app.route('/home')
def home():
    with driver.session() as neo4j_session:
        # 原有统计
        total_herbs = neo4j_session.run("MATCH (n:Herb) RETURN count(n) AS c").single()['c']
        total_prescriptions = neo4j_session.run("MATCH (n:Prescription) RETURN count(n) AS c").single()['c']
        total_diseases = neo4j_session.run("MATCH (n:Disease) RETURN count(n) AS c").single()['c']
        total_relations = neo4j_session.run("MATCH ()-[r]->() RETURN count(r) AS c").single()['c']

        # 新增：典籍总数
        total_sources = neo4j_session.run("MATCH (s:Literature) RETURN count(s) AS c").single()['c']

        # 典籍方剂排名
        source_ranking = neo4j_session.run("""
            MATCH (p:Prescription)-[:HAS_SOURCE]->(s:Literature)
            RETURN s.name AS name, count(p) AS value
            ORDER BY value DESC LIMIT 10
        """).data()
        source_ranking = [{'name': r['name'], 'value': r['value']} for r in source_ranking]

        # 方剂类型排名（需存在 Category 节点和 HAS_CATEGORY 关系）
        category_ranking = neo4j_session.run("""
            MATCH (p:Prescription)-[:HAS_CATEGORY]->(c:Category)
            RETURN c.name AS name, count(p) AS value
            ORDER BY value DESC LIMIT 10
        """).data()
        category_ranking = [{'name': r['name'], 'value': r['value']} for r in category_ranking]

    # 用户数（仅管理员可见）
    user_count = None
    if session.get('role') == 'admin':
        conn = sqlite3.connect(DB_PATH)
        user_count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        conn.close()

    return render_template('home.html', active_page='home',
                           total_herbs=total_herbs, total_prescriptions=total_prescriptions,
                           total_diseases=total_diseases, total_relations=total_relations,
                           total_sources=total_sources,
                           source_ranking=source_ranking,
                           category_ranking=category_ranking,
                           user_count=user_count)



@app.route('/answer', methods=['GET', 'POST'])
def answer():
    graph_data, table_data, search_keyword = {}, [], ""
    if request.method == 'POST':
        raw_input = request.form.get('symptom', '').strip()
        search_keyword = raw_input
        if raw_input:
            cypher_query = """
            MATCH (d:Disease) WHERE d.name CONTAINS $symptom
            MATCH (p:Prescription)-[:TREATS]->(d)
            MATCH (p)-[r_comp:COMPOSED_OF]->(h:Herb)
            OPTIONAL MATCH (p)-[:HAS_SOURCE]->(src)
            OPTIONAL MATCH (d)-[:HAS_SYMPTOM]->(sym:Symptom)
            RETURN p, d, h, r_comp, src, sym LIMIT 200
            """
            with driver.session() as neo4j_session:
                records = list(neo4j_session.run(cypher_query, symptom=search_keyword))

            if records:
                nodes_dict, links, category_map, seen_table_rows = {}, [], {}, set()
                for rec in records:
                    def process(n, cat_fix=None):
                        if not n: return None
                        fmt = format_node(n, cat_fix)
                        nodes_dict[fmt['id']] = fmt
                        category_map[fmt['category']] = fmt['itemStyle']['color']
                        return fmt['id']

                    p_id = process(rec['p'], "Prescription")
                    d_id = process(rec['d'], "Disease")
                    h_id = process(rec['h'], "Herb")
                    src_id = process(rec['src'], "Source")
                    sym_id = process(rec['sym'], "Symptom")

                    links.append({"source": p_id, "target": d_id, "name": "治疗"})
                    links.append({"source": p_id, "target": h_id, "name": rec['r_comp'].get('dosage', '组成')})
                    if src_id: links.append({"source": p_id, "target": src_id, "name": "出处"})
                    if sym_id: links.append({"source": d_id, "target": sym_id, "name": "症状"})

                    p_name, h_name = rec['p'].get('name'), rec['h'].get('name')
                    if (p_name, h_name) not in seen_table_rows:
                        table_data.append({
                            "方名": p_name,
                            "出处": rec['src'].get('name', '未知') if rec['src'] else '未知',
                            "主治疾病": rec['d'].get('name'),
                            "中药": h_name,
                            "剂量": rec['r_comp'].get('dosage', '适量')
                        })
                        seen_table_rows.add((p_name, h_name))

                categories = [{"name": name, "itemStyle": {"color": color}} for name, color in category_map.items()]
                graph_data = {"nodes": list(nodes_dict.values()), "links": links, "categories": categories}
                save_history(raw_input, "answer", True)
            else:
                save_history(raw_input, "answer", False)
    return render_template('answer.html', graph_data=graph_data, table_data=table_data,
                           search_keyword=search_keyword, active_page='answer')

@app.route('/ask_ollama', methods=['POST'])
def ask_ollama():
    symptom = request.form.get('symptom')
    return jsonify({"result": call_ollama(f"中医分析：{symptom}。分析病因并推荐方剂。")})

@app.route('/inference', methods=['GET', 'POST'])
def inference():
    graph_data, search_keyword = {}, ""
    if request.method == 'POST':
        query = request.form.get('symptom', '').strip()
        search_keyword = query
        names = [n for n in re.split(r'\s+|和|与', query) if n]
        cypher, params = "", {}
        if len(names) == 1:
            cypher = "MATCH (p:Prescription {name: $name}) OPTIONAL MATCH (p)-[r]->(n) RETURN p, r, n LIMIT 200"
            params = {"name": names[0]}
        elif len(names) >= 2:
            cypher = "MATCH (p:Prescription) WHERE p.name IN [$n1, $n2] OPTIONAL MATCH (p)-[r]->(n) WHERE type(r) IN ['COMPOSED_OF', 'TREATS', 'HAS_SOURCE'] RETURN p, r, n LIMIT 200"
            params = {"n1": names[0], "n2": names[1]}

        if cypher:
            with driver.session() as neo4j_session:
                records = list(neo4j_session.run(cypher, **params))
            if records:
                nodes_dict, links, category_map = {}, [], {}
                for rec in records:
                    if not rec['p']: continue
                    p_fmt = format_node(rec['p'], "Prescription")
                    nodes_dict[p_fmt['id']] = p_fmt
                    category_map["Prescription"] = p_fmt['itemStyle']['color']
                    if rec['n']:
                        t_fmt = format_node(rec['n'])
                        nodes_dict[t_fmt['id']] = t_fmt
                        category_map[t_fmt['category']] = t_fmt['itemStyle']['color']
                        rel_show = rec['r'].get('dosage', rec['r'].type) if rec['r'].type == "COMPOSED_OF" else rec['r'].type
                        links.append({"source": p_fmt['id'], "target": t_fmt['id'], "name": rel_show})
                graph_data = {"nodes": list(nodes_dict.values()), "links": links,
                              "categories": [{"name": n, "itemStyle": {"color": c}} for n, c in category_map.items()]}
                save_history(query, "inference", True)
            else: save_history(query, "inference", False)
    return render_template('inference.html', graph_data=graph_data, search_keyword=search_keyword, active_page='inference')

@app.route('/suggest', methods=['POST'])
def suggest():
    symptom = request.form.get('symptom')
    return markdown.markdown(call_ollama(f"中医分析方剂：{symptom}。"))

@app.route('/herb_search', methods=['GET', 'POST'])
def herb_search():
    graph_data, related_prescriptions, search_term = None, [], ""
    if request.method == 'POST':
        search_term = request.form.get('herb_name', '').strip()
        if search_term:
            # 🌟 修改点 1：返回完整的节点 p 和 h，而不仅是名字
            query = "MATCH (p:Prescription)-[r]->(h:Herb {name: $name}) RETURN p, r.dosage AS dosage, r.processing AS processing, h LIMIT 50"
            try:
                with driver.session() as neo4j_session:
                    result = neo4j_session.run(query, name=search_term).data()
                if result:
                    nodes, links, node_names = [], [], set()

                    # 🌟 修改点 2：提取目标中药节点 h 的属性并注入 attributes
                    h_node = result[0]['h'] if result and 'h' in result[0] else {}
                    nodes.append({
                        "name": search_term,
                        "category": 0,
                        "symbolSize": 50,
                        "itemStyle": {"color": "#e74c3c"},
                        "label": {"show": True},
                        "attributes": dict(h_node)  # 注入所有属性
                    })
                    node_names.add(search_term)

                    for record in result:
                        p_node = record.get('p', {})
                        p_name = p_node.get('name', '未知')

                        if p_name not in node_names:
                            nodes.append({
                                "name": p_name,
                                "category": 1,
                                "symbolSize": 30,
                                "itemStyle": {"color": "#3498db"},
                                "label": {"show": True},
                                "attributes": dict(p_node)  # 🌟 注入所有属性
                            })
                            node_names.add(p_name)

                        links.append({"source": p_name, "target": search_term, "dosage": record.get('dosage', ''),
                                      "processing": record.get('processing', '')})
                        related_prescriptions.append({"name": p_name, "dosage": record.get('dosage', ''),
                                                      "processing": record.get('processing', '')})

                    graph_data = {"nodes": nodes, "links": links,
                                  "categories": [{"name": "查询药材"}, {"name": "关联方剂"}]}
                    # 成功记录历史
                    save_history(search_term, 'herb', True)
                else:
                    # 查询不到结果也记录一次历史
                    save_history(search_term, 'herb', False)
            except Exception as e:
                print(f"Neo4j错误: {e}")
    return render_template('herb_search.html', active_page='herb_search', graph_data=graph_data,
                           related_prescriptions=related_prescriptions, search_term=search_term)

@app.route('/dashboard')
def dashboard():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    stats = {}
    top_herbs = []
    top_diseases = []
    source_ranking = []   # 新增
    category_ranking = [] # 新增

    with driver.session() as neo4j_session:
        try:
            # 1. 统计各类节点数量（原有）
            res_counts = neo4j_session.run("MATCH (n) RETURN labels(n)[0] as label, count(n) as count")
            for record in res_counts:
                if record['label']:
                    label_map = {
                        "Herb": "中药",
                        "Prescription": "方剂",
                        "Disease": "疾病",
                        "Symptom": "症状",
                        "Efficacy": "功效",
                        "Category": "分类",
                        "Literature": "典籍",  # 新增
                        "Origin": "产地",  # 新增（或“来源”）
                        "Contraindication": "禁忌",  # 新增
                        "Department": "科室"  # 新增（根据实际含义调整）
                    }
                    name = label_map.get(record['label'], record['label'])
                    stats[name] = record['count']

            # 2. 统计最常用的 Top 10 中药（原有）
            res_herbs = neo4j_session.run("""
                MATCH (p:Prescription)-[:COMPOSED_OF]->(h:Herb) 
                RETURN h.name as name, count(p) as value 
                ORDER BY value DESC LIMIT 10
            """)
            top_herbs = [{"name": r["name"], "value": r["value"]} for r in res_herbs]

            # 3. 统计最常见的 Top 10 主治疾病（原有）
            res_diseases = neo4j_session.run("""
                MATCH (p:Prescription)-[:TREATS]->(d:Disease) 
                RETURN d.name as name, count(p) as value 
                ORDER BY value DESC LIMIT 10
            """)
            top_diseases = [{"name": r["name"], "value": r["value"]} for r in res_diseases]

            # ========== 新增部分 ==========
            # 4. 典籍收录方剂排名
            res_source = neo4j_session.run("""
                MATCH (p:Prescription)-[:HAS_SOURCE]->(s:Literature)
                RETURN s.name AS name, count(p) AS value
                ORDER BY value DESC LIMIT 10
            """)
            source_ranking = [{"name": r["name"], "value": r["value"]} for r in res_source]

            # 5. 方剂类型排名
            res_category = neo4j_session.run("""
                MATCH (p:Prescription)-[:HAS_CATEGORY]->(c:Category)
                RETURN c.name AS name, count(p) AS value
                ORDER BY value DESC LIMIT 10
            """)
            category_ranking = [{"name": r["name"], "value": r["value"]} for r in res_category]

        except Exception as e:
            print(f"数据大屏查询失败: {e}")

    nodes_pie_data = [{"name": k, "value": v} for k, v in stats.items()]

    # 将新增数据也传递给模板
    return render_template('dashboard.html',
                           active_page='dashboard',
                           nodes_pie_data=nodes_pie_data,
                           top_herbs=top_herbs,
                           top_diseases=top_diseases,
                           source_ranking=source_ranking,
                           category_ranking=category_ranking)

@app.route('/wordcloud')
def wordcloud(): return render_template('wordcloud.html', active_page='wordcloud')


# 🌟 修改后的词云接口，支持多种维度
@app.route('/api/wordcloud_data')
def wordcloud_data():
    cloud_type = request.args.get('type', 'efficacy')

    with driver.session() as neo4j_session:
        if cloud_type == 'symptom':
            # 症状频率（疾病包含的症状）
            cypher = "MATCH (d:Disease)-[:HAS_SYMPTOM]->(s:Symptom) RETURN s.name AS name, COUNT(d) AS value ORDER BY value DESC LIMIT 100"
        elif cloud_type == 'herb':
            # 高频中药（方剂中最常用的中药）
            cypher = "MATCH (p:Prescription)-[:COMPOSED_OF]->(h:Herb) RETURN h.name AS name, COUNT(p) AS value ORDER BY value DESC LIMIT 100"
        elif cloud_type == 'disease':
            # 主治疾病（方剂最常治疗的疾病）
            cypher = "MATCH (p:Prescription)-[:TREATS]->(d:Disease) RETURN d.name AS name, COUNT(p) AS value ORDER BY value DESC LIMIT 100"
        else:
            # 默认：中药功效（中药最常见的功效）
            cypher = "MATCH (h:Herb)-[:HAS_EFFICACY]->(e:Efficacy) RETURN e.name AS name, COUNT(h) AS value ORDER BY value DESC LIMIT 100"

        try:
            r = neo4j_session.run(cypher)
            return jsonify([{"name": x["name"], "value": x["value"]} for x in r])
        except Exception as e:
            print(f"词云查询失败: {e}")
            return jsonify([])


#  新增：智能 AI 对话接口
@app.route('/ai_chat', methods=['GET', 'POST'])
def ai_chat():
    if request.method == 'POST':
        user_input = request.json.get('question')

        # --- 1. 从 Neo4j 中检索相关图谱上下文 ---
        context_data = []
        try:
            with driver.session() as neo4j_session:
                # 巧妙的 Cypher 查询：检查用户输入中是否包含了图谱中长度>1的节点名称
                # 如果包含，则把该节点及其直接关联的节点都查出来
                cypher_query = """
                MATCH (n)
                WHERE size(n.name) > 1 AND $user_input CONTAINS n.name
                OPTIONAL MATCH (n)-[r]->(m)
                RETURN n.name AS source, labels(n)[0] AS n_label, 
                       type(r) AS rel, 
                       m.name AS target, labels(m)[0] AS m_label
                LIMIT 50
                """
                results = neo4j_session.run(cypher_query, user_input=user_input)

                for record in results:
                    source = f"{record['source']}({record['n_label']})"
                    if record['target']:
                        target = f"{record['target']}({record['m_label']})"
                        rel = record['rel']
                        # 翻译一下英文关系为中文，方便 AI 理解（根据你的图谱可适当增删）
                        rel_map = {
                            "COMPOSED_OF": "包含中药", "TREATS": "治疗疾病",
                            "HAS_SYMPTOM": "具有症状", "HAS_EFFICACY": "具有功效",
                            "HAS_SOURCE": "出自典籍", "HAS_CATEGORY": "属于分类"
                        }
                        rel_zh = rel_map.get(rel, rel)
                        context_data.append(f"已知: {source} -[{rel_zh}]-> {target}")
                    else:
                        context_data.append(f"已知图谱中存在: {source}")
        except Exception as e:
            print(f"图谱检索失败: {e}")

        # 对上下文去重并拼接为字符串
        context_str = "\n".join(list(set(context_data)))

        # --- 2. 构建包含图谱上下文的 Prompt 发给大模型 ---
        if context_str:
            prompt = (
                f"你是一个专业的中医智能助手。请务必优先根据以下【中医知识图谱上下文】来回答用户的问题。\n\n"
                f"【中医知识图谱上下文】：\n{context_str}\n\n"
                f"【用户问题】：{user_input}\n\n"
                f"要求：回答要专业、清晰。如果上下文中有相关信息，请明确引用；如果上下文信息不足，再结合你的中医知识补充，但不要生造图谱中没有的方剂组成。"
            )
        else:
            # 如果没匹配到图谱内容，降级为普通对话
            prompt = f"你是一个专业的中医智能助手。请用中医专家的口吻回答用户问题：{user_input}"

        # --- 3. 调用 Ollama 并保存历史 ---
        ai_reply = call_ollama(prompt)

        # 尝试保存到数据库（确保你的 chat_history 表结构允许为空，或根据你的实现调整）
        try:
            conn = sqlite3.connect(DB_PATH)
            conn.execute(
                "INSERT INTO chat_history (user_id, question, answer) VALUES (?, ?, ?)",
                (session.get('user_id', 1), user_input, ai_reply)  # 默认赋予1防止未登录报错
            )
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"保存对话历史失败: {e}")

        return jsonify({"reply": markdown.markdown(ai_reply)})

    # GET 请求：渲染聊天页面
    history = []
    if 'user_id' in session:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        history = conn.execute(
            "SELECT question, answer FROM chat_history WHERE user_id = ? ORDER BY timestamp ASC",
            (session.get('user_id'),)
        ).fetchall()
        conn.close()

    return render_template('ai_chat.html', chat_history=history, current_page='ai_chat',active_page='ai_chat')

#添加科室模块
@app.route('/department_search', methods=['GET', 'POST'])
def department_search():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    # 1. 从 Neo4j 获取真实的 Department 节点
    all_departments = []
    try:
        with driver.session() as neo4j_session:
            # 使用正确的 Department 标签
            dept_list_query = "MATCH (d:Department) RETURN DISTINCT d.name AS name ORDER BY d.name"
            dept_result = neo4j_session.run(dept_list_query)
            all_departments = [r['name'] for r in dept_result if r['name']]
    except Exception as e:
        print(f"获取科室列表失败: {e}")

    graph_data, related_nodes, search_term = None, [], ""

    # 2. 处理查询请求
    if request.method == 'POST':
        search_term = request.form.get('department_name', '').strip()
        if search_term:
            # 查询指定的 Department 及其所有关联的节点（比如 Disease 等）
            query = """
            MATCH (dept:Department {name: $name})-[r]-(n)
            RETURN dept, r, n LIMIT 150
            """
            try:
                with driver.session() as neo4j_session:
                    # 保留之前解决报错的精髓：不加 .data()，直接获取原生节点
                    result = neo4j_session.run(query, name=search_term)

                    nodes_dict, links = {}, []
                    for record in result:
                        dept_node = record['dept']
                        n_node = record['n']
                        r_rel = record['r']

                        # 格式化节点
                        dept_fmt = format_node(dept_node, "Department")
                        n_fmt = format_node(n_node)

                        nodes_dict[dept_fmt['id']] = dept_fmt
                        nodes_dict[n_fmt['id']] = n_fmt

                        links.append({
                            "source": dept_fmt['id'],
                            "target": n_fmt['id'],
                            "name": type(r_rel).__name__
                        })

                        related_nodes.append({
                            "name": n_fmt['name'],
                            "category": n_fmt['category'],
                            "relation": type(r_rel).__name__
                        })

                    categories = [{"name": c} for c in set(n['category'] for n in nodes_dict.values())]

                    if nodes_dict:
                        graph_data = {
                            "nodes": list(nodes_dict.values()),
                            "links": links,
                            "categories": categories
                        }
            except Exception as e:
                print(f"科室查询错误: {e}")

    return render_template('department_search.html',
                           active_page='department_search',
                           all_departments=all_departments,
                           graph_data=graph_data,
                           related_nodes=related_nodes,
                           search_term=search_term)



@app.route('/history')
def history_page():
    return render_template('history.html', inference_history=get_history('inference'),
                           answer_history=get_history('answer'), herb_history=get_history('herb'), active_page='history')

@app.route('/clear_history', methods=['POST'])
def clear_history_route():
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute('DELETE FROM query_history WHERE mode = ?', (request.form.get('mode'),))
        conn.commit()
        conn.close()
        return jsonify({'success': True})
    except: return jsonify({'success': False})

# ================= 用户权限模块 =================

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username, password = request.form['username'].strip(), request.form['password']
        if not username or not password:
            flash("用户名和密码不能为空！", "danger")
            return redirect(url_for('register'))
        try:
            conn = sqlite3.connect(DB_PATH)
            conn.execute("INSERT INTO users (username, password) VALUES (?, ?)", (username, password))
            conn.commit()
            flash("注册成功！请登录。", "success")
            return redirect(url_for('login'))
        except sqlite3.IntegrityError: flash("用户名已被占用！", "danger")
        finally: conn.close()
    return render_template('register.html')


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username, password = request.form['username'].strip(), request.form['password']
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        user = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        conn.close()
        if user and user['password'] == password:
            session.clear()
            session['user_id'] = user['id']
            session['username'] = user['username']
            session['role'] = user['role']          # 保存角色
            return redirect(url_for('home'))
        flash("用户名或密码错误！", "danger")
    return render_template('login.html')



@app.route('/logout')
def logout():
    # 清除用户的 session 信息（登录状态）
    session.clear()
    # 跳转回登录页面
    return redirect(url_for('login'))


@app.route('/profile', methods=['GET', 'POST'])
def profile():
    # 安全检查：没登录就踢回登录页
    if 'user_id' not in session:
        return redirect(url_for('login'))

    if request.method == 'POST':
        old = request.form.get('old_password')
        new = request.form.get('new_password')
        confirm = request.form.get('confirm_password')

        if new != confirm:
            flash("两次输入的新密码不一致", "danger")
            return redirect(url_for('profile'))

        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("SELECT password FROM users WHERE id = ?", (session['user_id'],))
        user = cur.fetchone()

        if user and user[0] == old:
            cur.execute("UPDATE users SET password = ? WHERE id = ?", (new, session['user_id']))
            conn.commit()
            flash("密码修改成功！", "success")
        else:
            flash("旧密码输入错误", "danger")

        conn.close()
        return redirect(url_for('profile'))

    # GET请求：直接渲染页面，并传 active_page='profile' 激活侧边栏高亮
    return render_template('profile.html', active_page='profile')







if __name__ == '__main__':
    app.run(debug=True, port=5000)