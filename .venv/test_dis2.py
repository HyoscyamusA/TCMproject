import json
import re
import csv

# --- 配置输入输出文件 ---
INPUT_FILE = 'xywy_all_diseases.json'
INTERMEDIATE_JSON = 'diseases_structured_final.json'

# 输出的 CSV 文件
FILE_NODE_DISEASE = 'nodes_diseases.csv'
FILE_NODE_SYMPTOM = 'nodes_symptoms.csv'
FILE_NODE_DEPT = 'nodes_departments.csv'
FILE_EDGE_SYMPTOM = 'edges_disease_symptom.csv'
FILE_EDGE_DEPT = 'edges_disease_department.csv'


# --- 工具函数：清洗长文本 ---
def clean_long_text(text):
    """
    清洗简介、病因、预防等长文本。
    1. 去除网页残留词。
    2. 将换行符替换为空格，防止 CSV 错行。
    """
    if not text or not isinstance(text, str):
        return "暂无数据"

    # 去除网页导航词
    text = text.replace("更多>", "").replace("详情>", "").replace("...", "")

    # 将所有换行符、回车符、连续空格替换为单个空格
    text = re.sub(r'[\r\n\s]+', ' ', text)

    return text.strip()


# --- 工具函数：提取列表 (症状/科室) ---
def extract_and_split(text, start_keywords, stop_keywords):
    if not text: return []

    start_pattern = f"(?:{'|'.join(start_keywords)})[：:]"
    stop_pattern = f"(?:{'|'.join(stop_keywords)}|$)"
    pattern = f"{start_pattern}\\s*(.*?)\\s*(?={stop_pattern})"

    match = re.search(pattern, text, re.DOTALL)
    if match:
        content = match.group(1).strip()
        content = content.replace("更多>", "").replace("...", "").replace("详情>", "")
        # 支持按 空格、顿号、逗号 拆分
        items = re.split(r'[\s、,，]+', content)
        # 过滤空值和过长噪音
        return [x.strip() for x in items if x.strip() and len(x.strip()) < 20]
    return []


# --- 第一步：解析数据并生成中间 JSON ---
def step1_generate_json():
    print(f"=== 第一步：解析原始数据 (去除 URL 和并发症) ===")
    structured_data = []

    with open(INPUT_FILE, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line: continue
            try:
                item = json.loads(line)

                raw_name = item.get('name', '').strip()
                raw_intro = item.get('简介', '')

                if not raw_name: continue

                # 1. 提取我们需要的属性 (清洗)
                clean_intro = clean_long_text(raw_intro)
                clean_cause = clean_long_text(item.get('病因', ''))
                clean_prev = clean_long_text(item.get('预防', ''))
                raw_cat = item.get('category', '其他')  # 保留分类

                # 注意：这里我们故意不获取 'url' 和 '并发症'，从而达到过滤目的

                # 2. 提取关系数据 (症状 & 科室)
                symptoms_list = extract_and_split(
                    raw_intro,
                    start_keywords=["症状表现", "临床表现", "症状"],
                    stop_keywords=["更多>", "并发疾病", "治疗", "就诊科室", "常用检查"]
                )

                depts_list = extract_and_split(
                    raw_intro,
                    start_keywords=["就诊科室"],
                    stop_keywords=["治疗", "常用药品", "护理", "检查", "更多>"]
                )

                # 3. 构建对象
                obj = {
                    "id": raw_name,
                    "name": raw_name,
                    "introduction": clean_intro,
                    "cause": clean_cause,
                    "prevention": clean_prev,
                    "category": raw_cat,
                    # 不包含 url 和 complications

                    "extracted_symptoms": symptoms_list,
                    "extracted_departments": depts_list
                }
                structured_data.append(obj)

            except json.JSONDecodeError:
                continue

    # 保存中间 JSON
    with open(INTERMEDIATE_JSON, 'w', encoding='utf-8') as f:
        json.dump(structured_data, f, ensure_ascii=False, indent=2)

    print(f"成功生成中间文件: {INTERMEDIATE_JSON}")


# --- 第二步：生成 CSV (解决乱码) ---
def step2_generate_csvs():
    print(f"\n=== 第二步：生成 CSV 文件 (使用 UTF-8-SIG 编码解决乱码) ===")

    try:
        with open(INTERMEDIATE_JSON, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except FileNotFoundError:
        print("未找到中间文件。")
        return

    # 集合定义 (用于去重)
    nodes_symptoms = set()
    nodes_departments = set()

    rels_symptom = []
    rels_dept = []

    for item in data:
        disease_name = item['name']

        for s in item['extracted_symptoms']:
            nodes_symptoms.add(s)
            rels_symptom.append([disease_name, s])

        for d in item['extracted_departments']:
            nodes_departments.add(d)
            rels_dept.append([disease_name, d])

    # --- 写入 CSV 函数 (关键修改：encoding='utf-8-sig') ---
    def write_to_csv(filename, headers, rows):
        print(f"写入文件: {filename} ...")
        # 这里的 utf-8-sig 是为了让 Excel 能正确识别中文
        with open(filename, 'w', encoding='utf-8-sig', newline='') as f:
            writer = csv.writer(f, quoting=csv.QUOTE_MINIMAL)
            writer.writerow(headers)
            if isinstance(rows, set):
                for r in rows:
                    writer.writerow([r])
            else:
                writer.writerows(rows)

    # 1. 写入疾病节点 (筛选后的属性)
    # 列：ID, 名称, 简介, 病因, 预防, 分类
    disease_rows = []
    for item in data:
        disease_rows.append([
            item['id'],
            item['name'],
            item['introduction'],
            item['cause'],
            item['prevention'],
            item['category']
        ])

    write_to_csv(FILE_NODE_DISEASE,
                 ['id', 'name', 'introduction', 'cause', 'prevention', 'category'],
                 disease_rows)

    # 2. 写入其他节点
    write_to_csv(FILE_NODE_SYMPTOM, ['name'], nodes_symptoms)
    write_to_csv(FILE_NODE_DEPT, ['name'], nodes_departments)

    # 3. 写入关系
    write_to_csv(FILE_EDGE_SYMPTOM, ['disease_id', 'symptom_name'], rels_symptom)
    write_to_csv(FILE_EDGE_DEPT, ['disease_id', 'department_name'], rels_dept)

    print("\n所有 CSV 文件生成完毕，请使用 Excel 打开检查。")


if __name__ == "__main__":
    step1_generate_json()
    step2_generate_csvs()