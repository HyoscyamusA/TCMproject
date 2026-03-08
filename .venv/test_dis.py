import json
import re
import csv

# --- 文件路径配置 ---
INPUT_FILE = 'xywy_all_diseases.json'  # 原始数据
INTERMEDIATE_JSON = 'diseases_structured.json'  # 中间层：清洗后的JSON
# 输出的CSV文件
FILE_NODE_DISEASE = 'nodes_diseases.csv'  # 疾病节点(建议加上这个)
FILE_NODE_SYMPTOM = 'nodes_symptoms.csv'  # 症状节点
FILE_NODE_DEPT = 'nodes_departments.csv'  # 科室节点
FILE_EDGE_SYMPTOM = 'edges_disease_symptom.csv'  # 关系：疾病-症状
FILE_EDGE_DEPT = 'edges_disease_department.csv'  # 关系：疾病-科室


# --- 工具函数：通用提取与拆分 ---
def extract_and_split(text, start_keywords, stop_keywords):
    if not text:
        return []

    # 1. 正则截取段落
    start_pattern = f"(?:{'|'.join(start_keywords)})[：:]"
    stop_pattern = f"(?:{'|'.join(stop_keywords)}|$)"
    pattern = f"{start_pattern}\\s*(.*?)\\s*(?={stop_pattern})"

    match = re.search(pattern, text, re.DOTALL)

    if match:
        content = match.group(1).strip()
        # 清洗杂质
        content = content.replace("更多>", "").replace("...", "").replace("详情>", "")

        # 2. 拆分逻辑：支持 换行、空格、中文顿号、中文逗号、英文逗号
        # 核心正则：[\s、,，]+ 表示只要遇到空白或标点就切分
        items = re.split(r'[\s、,，]+', content)

        # 3. 过滤：去掉空值，去掉太长的噪音(超过20字通常不是标准词)
        clean_items = [x.strip() for x in items if x.strip() and len(x.strip()) < 20]
        return clean_items

    return []


# --- 第一步：生成结构化 JSON ---
def step1_generate_json():
    print(f"=== 第一步：正在解析 {INPUT_FILE} 生成中间 JSON ===")
    structured_data = []

    with open(INPUT_FILE, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line: continue
            try:
                item = json.loads(line)
                name = item.get('name', '').strip()
                intro = item.get('简介', '')

                if not name or not intro:
                    continue

                # 提取症状列表
                symptoms = extract_and_split(
                    intro,
                    start_keywords=["症状表现", "临床表现", "症状"],
                    stop_keywords=["更多>", "并发疾病", "治疗", "就诊科室", "常用检查"]
                )

                # 提取科室列表
                departments = extract_and_split(
                    intro,
                    start_keywords=["就诊科室"],
                    stop_keywords=["治疗", "常用药品", "护理", "检查", "更多>"]
                )

                # 构建清洗后的对象
                obj = {
                    "name": name,
                    "symptoms": symptoms,  # 这是一个 list ["咳嗽", "发热"]
                    "departments": departments,  # 这是一个 list ["内科", "呼吸内科"]
                    "category": item.get('category', '未知')  # 保留分类备用
                }
                structured_data.append(obj)

            except json.JSONDecodeError:
                continue

    # 保存为标准的 JSON 数组格式，方便查看
    with open(INTERMEDIATE_JSON, 'w', encoding='utf-8') as f:
        json.dump(structured_data, f, ensure_ascii=False, indent=2)

    print(f"成功！已生成中间文件: {INTERMEDIATE_JSON} (共 {len(structured_data)} 条)")


# --- 第二步：将 JSON 转换为 CSV ---
def step2_json_to_csv():
    print(f"\n=== 第二步：正在读取 {INTERMEDIATE_JSON} 生成 CSV ===")

    # 读取中间 JSON
    try:
        with open(INTERMEDIATE_JSON, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except FileNotFoundError:
        print("错误：找不到中间 JSON 文件，请先运行第一步。")
        return

    # 准备容器 (使用 set 去重)
    nodes_symptoms = set()
    nodes_departments = set()
    # 疾病节点其实就是 data 里的 name，不需要额外 set，但为了 CSV 规范可以生成一个

    rels_symptom = []  # [疾病, 症状]
    rels_department = []  # [疾病, 科室]

    # 遍历数据
    for item in data:
        disease = item['name']

        # 处理症状
        for s in item['symptoms']:
            nodes_symptoms.add(s)
            rels_symptom.append([disease, s])

        # 处理科室
        for d in item['departments']:
            nodes_departments.add(d)
            rels_department.append([disease, d])

    # --- 辅助写入函数 ---
    def write_csv(filename, headers, rows):
        with open(filename, 'w', encoding='utf-8', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(headers)
            if isinstance(rows, set):
                for r in rows:
                    writer.writerow([r])
            else:
                writer.writerows(rows)
        print(f"-> 生成文件: {filename} ({len(rows)} 条)")

    # 执行写入
    # 1. 疾病节点 (可选，但推荐有)
    disease_list = [[item['name']] for item in data]
    write_csv(FILE_NODE_DISEASE, ['disease_name'], disease_list)

    # 2. 症状节点
    write_csv(FILE_NODE_SYMPTOM, ['symptom_name'], nodes_symptoms)

    # 3. 科室节点
    write_csv(FILE_NODE_DEPT, ['department_name'], nodes_departments)

    # 4. 关系: 疾病-症状
    write_csv(FILE_EDGE_SYMPTOM, ['disease_name', 'symptom_name'], rels_symptom)

    # 5. 关系: 疾病-科室
    write_csv(FILE_EDGE_DEPT, ['disease_name', 'department_name'], rels_department)

    print("\n所有 CSV 文件生成完毕！")


if __name__ == "__main__":
    # 按顺序执行
    step1_generate_json()
    step2_json_to_csv()