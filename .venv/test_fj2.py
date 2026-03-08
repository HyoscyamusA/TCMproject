import pandas as pd
from openai import OpenAI
import json
import time
import os
import csv
import httpx
import glob  # 用于查找文件夹里的所有文件

# ================= ⚙️ 配置区域 =================

API_KEY = "sk-mpgfuabxekkslpreghzyehsgrnsvtwthmalwlxqlsdlndxwg"
BASE_URL = "https://api.siliconflow.cn/v1"
MODEL_NAME = "Qwen/Qwen2.5-72B-Instruct"

# 1. 输入文件夹路径 (注意这里写的是文件夹路径)
INPUT_DIR = r"E:\project\TCM_project1\.venv\data_fangji"

# 2. 输出文件路径 (生成的 CSV)
OUTPUT_NODE_FILE = r"E:\project\TCM_project1\.venv\clean_data\node_prescription_final.csv"
OUTPUT_REL_FILE = r"E:\project\TCM_project1\.venv\clean_data\relations_all_final.csv"
FAILED_FILE = r"E:\project\TCM_project1\.venv\clean_data\failed_records.csv"

# ================= 🧠 LLM 核心逻辑 (保持不变) =================

http_client = httpx.Client(timeout=300.0)
client = OpenAI(api_key=API_KEY, base_url=BASE_URL, http_client=http_client)


def smart_process_row(raw_data_dict):
    # 过滤空值，减少 token
    clean_input = {k: v for k, v in raw_data_dict.items() if v and str(v).strip() != ""}
    input_text = json.dumps(clean_input, ensure_ascii=False)

    prompt = f"""
    你是一个中医知识图谱构建专家。请分析输入数据，提取【方剂节点属性】和【关联实体关系】。

    **输入数据：**
    {input_text}

    **任务 1：提取方剂属性 (Node)**
    标准化以下字段，没有则留空：
    - 名称, 功效, 主治, 组成原文, 用法用量, 方解, 要点, 加减化裁, 注意事项, 临床应用(包含医案), 其他信息。

    **任务 2：提取实体关系 (Edges)**
    请提取以下三类关系，输出到 edges 数组中：
    1. **组成关系 (composition)**: 提取药物及剂量。Target是药材名。
       - 规则：补全省略的剂量（如“各十克”），保留量词（如“百枚”）。
    2. **出处关系 (source)**: 提取典籍/文献名。Target是书名（如《伤寒论》）。
       - 规则：去掉书名号。
    3. **类别关系 (category)**: 提取方剂分类。Target是分类名（如“解表剂”）。

    **输出格式（Strict JSON）：**
    {{
        "node": {{
            "名称": "...", "功效": "...", "主治": "...", "组成原文": "...", 
            "用法用量": "...", "方解": "...", "要点": "...", "加减化裁": "...", 
            "注意事项": "...", "临床应用": "...", "其他信息": "..."
        }},
        "edges": [
            {{"target": "桂枝", "type": "Herb", "relation": "composition", "property": "三两"}},
            {{"target": "伤寒论", "type": "Source", "relation": "source", "property": ""}},
            {{"target": "解表剂", "type": "Category", "relation": "category", "property": ""}}
        ]
    }}
    """

    for attempt in range(3):
        try:
            response = client.chat.completions.create(
                model=MODEL_NAME,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=4096,
                response_format={"type": "json_object"}
            )
            return json.loads(response.choices[0].message.content)
        except Exception:
            time.sleep(3)
    return None


# ================= ▶️ 主程序 (读取逻辑已修改) =================

if __name__ == "__main__":
    # 1. 检查输入文件夹
    if not os.path.exists(INPUT_DIR):
        print(f"❌ 找不到文件夹: {INPUT_DIR}")
        exit()

    # 2. 获取所有 JSON 文件列表
    # 也就是 E:\project\TCM_project1\.venv\data_fangji\*.json
    json_files = glob.glob(os.path.join(INPUT_DIR, "*.json"))

    if not json_files:
        print(f"⚠️ 文件夹里没有找到 .json 文件！请检查扩展名。")
        exit()

    print(f"🚀 扫描到 {len(json_files)} 个 JSON 文件，准备处理...")

    # 3. 检查断点续传 (读取已处理的方剂名)
    processed_names = set()
    if os.path.exists(OUTPUT_NODE_FILE):
        try:
            done_df = pd.read_csv(OUTPUT_NODE_FILE)
            if '名称' in done_df.columns:
                processed_names = set(done_df['名称'].unique())
            print(f"🔄 检测到已处理 {len(processed_names)} 条，将自动跳过。")
        except:
            pass

    # 4. 准备写入 CSV
    # 确保输出目录存在
    os.makedirs(os.path.dirname(OUTPUT_NODE_FILE), exist_ok=True)

    with open(OUTPUT_NODE_FILE, 'a', newline='', encoding='utf-8-sig') as f_node, \
            open(OUTPUT_REL_FILE, 'a', newline='', encoding='utf-8-sig') as f_rel, \
            open(FAILED_FILE, 'a', newline='', encoding='utf-8-sig') as f_fail:

        # 定义表头
        node_headers = ['名称', '功效', '主治', '组成原文', '用法用量',
                        '方解', '要点', '加减化裁', '注意事项', '临床应用', '其他信息']
        node_writer = csv.DictWriter(f_node, fieldnames=node_headers)

        rel_writer = csv.writer(f_rel)
        rel_header = ['source_prescription', 'target_entity', 'target_type', 'relation_type', 'property']

        fail_writer = csv.writer(f_fail)

        # 写表头 (如果是新文件)
        if os.path.getsize(OUTPUT_NODE_FILE) == 0:
            node_writer.writeheader()
        if os.path.getsize(OUTPUT_REL_FILE) == 0:
            rel_writer.writerow(rel_header)

        # 5. 遍历每个 JSON 文件
        count = 0
        total_files = len(json_files)

        for json_path in json_files:
            file_name = os.path.basename(json_path)

            # 读取 JSON 内容
            try:
                with open(json_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
            except Exception as e:
                print(f"❌ 无法读取文件 {file_name}: {e}")
                continue

            # 处理数据结构：不管 JSON 里面是单个对象，还是一个列表，统一转成列表处理
            if isinstance(data, dict):
                items = [data]
            elif isinstance(data, list):
                items = data
            else:
                print(f"⚠️ 文件格式无法识别: {file_name}")
                continue

            # 遍历文件里的每一条方剂数据
            for item in items:
                # 获取方名用于显示进度 (尝试不同可能的键名)
                p_name = item.get('name') or item.get('方名') or item.get('名称') or file_name.replace('.json', '')

                # 如果已处理过，跳过
                if p_name in processed_names:
                    continue

                count += 1
                print(f"[{count}] 处理文件: {file_name} -> 方剂: {p_name} ... ", end="", flush=True)
                start_t = time.time()

                # === 核心调用 ===
                result = smart_process_row(item)

                if result:
                    # 写入 Node
                    node_data = result.get("node", {})
                    clean_node = {k: node_data.get(k, "") for k in node_headers}
                    # 兜底：如果 LLM 没提取到名字，强制使用源数据的名字
                    if not clean_node['名称']: clean_node['名称'] = p_name

                    node_writer.writerow(clean_node)
                    f_node.flush()

                    # 写入 Edges
                    edges = result.get("edges", [])
                    for edge in edges:
                        if edge.get('target'):
                            rel_writer.writerow([
                                clean_node['名称'],
                                edge.get('target'),
                                edge.get('type'),  # Herb / Source / Category
                                edge.get('relation'),  # composition / source / category
                                edge.get('property')
                            ])
                    f_rel.flush()

                    print(f"✅ [耗时{time.time() - start_t:.1f}s]")
                else:
                    print("❌ 失败 (API返回空)")
                    fail_writer.writerow([file_name, p_name, "LLM Empty"])
                    f_fail.flush()

                # 稍作休息
                time.sleep(0.5)

    print("\n🎉 全部文件处理完成！")