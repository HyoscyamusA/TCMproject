from flask import Flask, request, render_template, jsonify
from neo4j import GraphDatabase
import os
import sqlite3
from datetime import datetime
import requests
import json

app = Flask(__name__)

# ================= ⚙️ 配置区 (请根据实际情况修改) =================

# 1. 数据库路径 (SQLite 用于存历史记录)
DB_PATH = os.path.join(os.path.dirname(__file__), 'TCM.db')

# 2. Neo4j 配置
NEO4J_URI = "bolt://localhost:7687"
NEO4J_AUTH = ("neo4j", "12345678")  # ⚠️⚠️⚠️ 请务必修改成你的真实密码

# 3. Ollama 配置 (本地大模型)
OLLAMA_API_URL = "http://localhost:11434/api/generate"
# 你本地运行的模型名称，例如: 'qwen2.5', 'llama3', 'deepseek-r1' 等
OLLAMA_MODEL = "qwen2.5:1.5b"

# 4. 颜色配置 (适配你的全量图谱)
COLOR_MAP = {
    "Prescription": "#87EBC1",  # 方剂
    "Herb": "#69B2FF",  # 中药
    "Disease": "#EE90A1",  # 疾病
    "Symptom": "#FF9F40",  # 症状
    "Department": "#FFCD56",  # 科室
    "Efficacy": "#16C2D5",  # 功效
    "Origin": "#9A66E4",  # 产地
    "Category": "#FF6B6B",  # 类别
    "Literature": "#A0A0A0",  # 文献
    "Contraindication": "#FF4D4D",  # 禁忌
    "Flavor": "#4BC0C0",  # 性味
    "Meridian": "#9966FF"  # 归经
}

# ================= 🛠️ 数据库工具函数 =================

driver = GraphDatabase.driver(NEO4J_URI, auth=NEO4J_AUTH)


def init_sqlite_db():
    """初始化 SQLite 历史记录表"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS query_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mode TEXT,
            query TEXT,
            result TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()


init_sqlite_db()


def save_history(mode, query, result_summary):
    """保存查询历史"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute('INSERT INTO query_history (mode, query, result) VALUES (?, ?, ?)',
                       (mode, query, result_summary))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"历史记录保存失败: {e}")


def get_history(mode):
    """获取历史记录"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute(
            'SELECT query, result, timestamp FROM query_history WHERE mode = ? ORDER BY timestamp DESC LIMIT 20',
            (mode,))
        rows = cursor.fetchall()
        conn.close()
        return [{'query': r[0], 'has_result': bool(r[1]), 'timestamp': r[2]} for r in rows]
    except:
        return []


# ================= 🤖 Ollama 调用函数 =================

def call_ollama(prompt):
    """调用本地 Ollama 接口生成回答"""
    try:
        payload = {
            "model": OLLAMA_MODEL,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": 0.7,
                "num_ctx": 4096  # 上下文长度
            }
        }
        print(f"正在调用 Ollama ({OLLAMA_MODEL})...")
        response = requests.post(OLLAMA_API_URL, json=payload, timeout=60)

        if response.status_code == 200:
            result = response.json()
            return result.get("response", "模型未返回内容")
        else:
            return f"Ollama 调用失败: {response.status_code} - {response.text}"

    except requests.exceptions.ConnectionError:
        return "错误：无法连接到 Ollama，请确认本地 Ollama 服务已启动 (默认端口 11434)。"
    except Exception as e:
        return f"AI 生成出错: {str(e)}"


# ================= 🌐 路由定义 =================

@app.route('/')
def home():
    return render_template('home.html', active_page='home')


# --- 1. 推理模式 (方剂对比) ---
@app.route('/inference', methods=['GET', 'POST'])
def inference():
    graph_data = None
    table_data = []

    if request.method == 'POST':
        user_input = request.form.get('symptom', '').strip()

        # 简单提取输入的两个方剂名（假设用空格或"和"分隔）
        keywords = user_input.replace('和', ' ').replace('与', ' ').split()
        # 过滤掉短词，保留可能的方剂名
        prescriptions = [k for k in keywords if len(k) >= 2][:2]

        if prescriptions:
            save_history('inference', user_input, "已查询")
            # Cypher: 查询方剂及其组成的药材（带剂量）
            query = """
            MATCH (p:Prescription)-[r:COMPOSED_OF]->(h:Herb)
            WHERE p.name IN $names
            RETURN p.name as p_name, h.name as h_name, r.dosage as dosage, h.主治 as h_func
            """
            with driver.session() as session:
                result = session.run(query, names=prescriptions)

                nodes = {}
                links = []
                # 记录每个方剂有哪些药
                pres_dict = {name: [] for name in prescriptions}

                for record in result:
                    p_name = record['p_name']
                    h_name = record['h_name']
                    dosage = record['dosage'] if record['dosage'] else "适量"
                    h_func = record['h_func'] if record['h_func'] else "暂无"

                    pres_dict[p_name].append({
                        '中药': h_name,
                        '剂量': dosage,
                        '中药功能主治': h_func
                    })

                    # 构建图谱节点
                    if p_name not in nodes:
                        nodes[p_name] = {"id": p_name, "name": p_name, "category": "方剂", "symbolSize": 50}

                    if h_name not in nodes:
                        nodes[h_name] = {"id": h_name, "name": h_name, "category": "中药", "symbolSize": 30}

                    # 连线：方剂 -> 中药
                    links.append({
                        "source": p_name,
                        "target": h_name,
                        "name": dosage  # 线上的文字显示剂量
                    })

                # 整理成 echarts 格式
                categories = [{"name": "方剂"}, {"name": "中药"}]
                graph_data = {
                    "nodes": list(nodes.values()),
                    "links": links,
                    "categories": categories
                }

                # 整理成表格数据
                for p_name, herbs in pres_dict.items():
                    for herb in herbs:
                        table_data.append({
                            '方名': p_name,
                            **herb
                        })

    return render_template('inference.html',
                           graph_data=graph_data,
                           table_data=table_data,
                           active_page='inference')


# --- 2. 问答模式 (症状 -> 疾病 -> 方剂) ---
@app.route('/answer', methods=['GET', 'POST'])
def answer():
    graph_data = None
    table_data = []

    if request.method == 'POST':
        symptom_input = request.form.get('symptom', '').strip()
        save_history('answer', symptom_input, "已查询")

        # Cypher: 症状 <- 疾病 <- 方剂
        # 限制路径长度和返回数量，防止图太大
        query = """
        MATCH (s:Symptom {name: $symptom})<-[:HAS_SYMPTOM]-(d:Disease)<-[:TREATS]-(p:Prescription)
        OPTIONAL MATCH (p)-[r:COMPOSED_OF]->(h:Herb)
        RETURN s.name as sym, d.name as dis, p.name as pres, 
               h.name as herb, r.dosage as dosage, h.主治 as h_func
        LIMIT 50
        """

        with driver.session() as session:
            result = session.run(query, symptom=symptom_input)

            nodes = {}
            links = []
            seen_links = set()

            # 表格数据缓存
            temp_table = {}

            for record in result:
                sym = record['sym']
                dis = record['dis']
                pres = record['pres']
                herb = record['herb']
                dosage = record['dosage'] or "适量"
                h_func = record['h_func'] or ""

                # 1. 症状节点
                if sym not in nodes: nodes[sym] = {"id": sym, "name": sym, "category": "症状", "symbolSize": 40}
                # 2. 疾病节点
                if dis not in nodes: nodes[dis] = {"id": dis, "name": dis, "category": "疾病", "symbolSize": 45}
                # 3. 方剂节点
                if pres not in nodes: nodes[pres] = {"id": pres, "name": pres, "category": "方剂", "symbolSize": 50}

                # 关系: 症状 <- 疾病
                if (dis, sym) not in seen_links:
                    links.append({"source": dis, "target": sym, "name": "包含症状"})
                    seen_links.add((dis, sym))

                # 关系: 方剂 -> 疾病
                if (pres, dis) not in seen_links:
                    links.append({"source": pres, "target": dis, "name": "治疗"})
                    seen_links.add((pres, dis))

                # 如果有中药信息，也加进图谱（可选，为了不让图太乱，这里只加到表格）
                # 为了表格显示：
                if pres not in temp_table: temp_table[pres] = []
                if herb and not any(x['中药'] == herb for x in temp_table[pres]):
                    temp_table[pres].append({
                        '中药': herb, '剂量': dosage, '中药功能主治': h_func
                    })

            # 整理表格
            for p_name, herbs in temp_table.items():
                for h in herbs:
                    table_data.append({'方名': p_name, **h})

            if nodes:
                categories = [{"name": "症状"}, {"name": "疾病"}, {"name": "方剂"}]
                # 给节点上色
                for n in nodes.values():
                    n['itemStyle'] = {"color": COLOR_MAP.get(n['category'], '#ccc')}

                graph_data = {
                    "nodes": list(nodes.values()),
                    "links": links,
                    "categories": categories
                }

    return render_template('answer.html',
                           graph_data=graph_data,
                           table_data=table_data,
                           active_page='answer')


# --- 3. 药材搜索 (全量属性展示) ---
@app.route('/herb_search', methods=['GET', 'POST'])
def herb_search():
    graph_data = None
    herb_info = {}

    if request.method == 'POST':
        herb_name = request.form.get('herb_name', '').strip()
        save_history('herb', herb_name, "已查询")

        # 查询中药节点及其所有向外连出的关系
        query = """
        MATCH (h:Herb {name: $name})-[r]->(n)
        RETURN h, type(r) as rel_type, n, labels(n) as lbls
        """

        with driver.session() as session:
            result = session.run(query, name=herb_name)

            nodes = {}
            links = []

            # 初始化中心中药节点
            nodes[herb_name] = {"id": herb_name, "name": herb_name, "category": "Herb", "symbolSize": 60}

            info_collected = False

            for record in result:
                h_node = record['h']
                rel_type = record['rel_type']
                target_node = record['n']
                target_labels = record['lbls']

                # 提取中药属性用于表格展示
                if not info_collected:
                    herb_info = dict(h_node)  # 获取性味、入药部位等
                    info_collected = True

                t_name = target_node.get('name', '未知节点')
                # 确定目标节点的类别 (取第一个Label)
                t_category = target_labels[0] if target_labels else "Other"

                # 添加目标节点
                if t_name not in nodes:
                    nodes[t_name] = {
                        "id": t_name,
                        "name": t_name,
                        "category": t_category,
                        "symbolSize": 30
                    }

                # 翻译关系名称用于显示
                rel_display = rel_type
                if rel_type == "HAS_EFFICACY": rel_display = "功效"
                if rel_type == "PRODUCED_IN": rel_display = "产地"
                if rel_type == "HAS_CONTRAINDICATION": rel_display = "禁忌"
                if rel_type == "CITED_BY": rel_display = "文献"
                if rel_type == "HAS_CATEGORY": rel_display = "分类"
                if rel_type == "HAS_FLAVOR": rel_display = "性味"
                if rel_type == "HAS_MERIDIAN": rel_display = "归经"
                if rel_type == "TREATS": rel_display = "治疗"

                links.append({
                    "source": herb_name,
                    "target": t_name,
                    "name": rel_display
                })

            if len(nodes) > 1:
                # 动态生成 Echarts Categories
                unique_cats = set(n['category'] for n in nodes.values())
                categories = [{"name": c} for c in unique_cats]

                # 上色
                for n in nodes.values():
                    # 尝试用 COLOR_MAP，如果没有则随机或灰色
                    n['itemStyle'] = {"color": COLOR_MAP.get(n['category'], '#999')}

                graph_data = {
                    "nodes": list(nodes.values()),
                    "links": links,
                    "categories": categories
                }
            else:
                # 如果没查到关系，尝试只查节点本身
                res = session.run("MATCH (h:Herb {name: $name}) RETURN h", name=herb_name)
                rec = res.single()
                if rec:
                    herb_info = dict(rec['h'])

    return render_template('herb_search.html',
                           graph_data=graph_data,
                           herb_info=herb_info,
                           active_page='herb')


# --- 4. AI 建议接口 (Ollama) ---
@app.route('/suggest', methods=['POST'])
def suggest():
    mode = request.form.get('mode')
    symptom = request.form.get('symptom')

    prompt = ""
    if mode == 'inference':
        prompt = f"""
        作为一名资深中医师，请分析以下两个方剂或药材组合：【{symptom}】。
        请从以下几个方面进行对比和分析：
        1. 它们的组成有何异同？
        2. 它们的功效和主治重点有何区别？
        3. 如果将它们联合使用，是否合理？有什么需要注意的禁忌？
        请给出专业的临床建议。
        """
    elif mode == 'answer':
        prompt = f"""
        患者主诉症状为：【{symptom}】。
        作为一名中医师，请根据中医辨证理论：
        1. 分析可能的病机（是什么原因导致的）。
        2. 推荐1-2个经典方剂，并说明理由。
        3. 给出生活饮食调理建议。
        注意：这只是咨询，请提醒患者及时就医。
        """
    else:
        return "无效的请求模式"

    return call_ollama(prompt)


# --- 5. 数据大屏 ---
@app.route('/dashboard')
def dashboard():
    with driver.session() as session:
        # 统计各节点数量
        count_query = """
        MATCH (n) 
        RETURN labels(n)[0] as label, count(n) as count
        """
        counts = session.run(count_query)
        data_map = {
            'Herb': 0, 'Prescription': 0, 'Disease': 0, 'Symptom': 0
        }
        for r in counts:
            lbl = r['label']
            if lbl in data_map:
                data_map[lbl] = r['count']

        # 高频中药 (被方剂包含次数最多的)
        top_herb_query = """
        MATCH (p:Prescription)-[:COMPOSED_OF]->(h:Herb)
        RETURN h.name as name, count(p) as count
        ORDER BY count DESC LIMIT 10
        """
        top_herbs = session.run(top_herb_query)
        top_herbs_names = []
        top_herbs_counts = []
        for r in top_herbs:
            top_herbs_names.append(r['name'])
            top_herbs_counts.append(r['count'])

    return render_template('dashboard.html',
                           total_herbs=data_map['Herb'],
                           total_prescriptions=data_map['Prescription'],
                           total_diseases=data_map['Disease'],
                           total_symptoms=data_map['Symptom'],
                           top_herbs_names=top_herbs_names,
                           top_herbs_counts=top_herbs_counts,
                           active_page='dashboard'
                           )


# --- 6. 词云数据 ---
@app.route('/api/wordcloud_data')
def wordcloud_api():
    """返回中药功效词云数据"""
    with driver.session() as session:
        # 统计功效出现的频率
        # 假设 Efficacy 节点连接着 Herb
        query = """
        MATCH (h:Herb)-[:HAS_EFFICACY]->(e:Efficacy)
        RETURN e.name AS name, COUNT(h) AS value
        ORDER BY value DESC
        LIMIT 100
        """
        result = session.run(query)
        data = [{"name": record["name"], "value": record["value"]} for record in result]
        return jsonify(data)


# ================= 词云相关路由 =================

# 1. 页面路由：负责显示 HTML 页面
# layout.html 中的 {{ url_for('wordcloud') }} 就是找这个函数
@app.route('/wordcloud')
def wordcloud():
    return render_template('wordcloud.html', active_page='wordcloud')

# 2. 数据接口：负责返回 JSON 数据
# wordcloud.html 中的 fetch('/api/wordcloud_data') 就是找这个地址
@app.route('/api/wordcloud_data')
def wordcloud_data():
    try:
        with driver.session() as session:
            # 查询拥有某功效的中药数量，作为词云权重
            result = session.run("""
                MATCH (h:Herb)-[:has_efficacy]->(e:Efficacy)
                RETURN e.name AS name, COUNT(h) AS value
                ORDER BY value DESC
                LIMIT 100
            """)
            data = [{"name": record["name"], "value": record["value"]} for record in result]
            return jsonify(data)
    except Exception as e:
        print(f"Error getting wordcloud data: {e}")
        return jsonify({"error": str(e)})

# --- 7. 历史记录 ---
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
        cursor = conn.cursor()
        cursor.execute('DELETE FROM query_history WHERE mode = ?', (mode,))
        conn.commit()
        conn.close()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


if __name__ == '__main__':
    app.run(debug=True, port=5000)