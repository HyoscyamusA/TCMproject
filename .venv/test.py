import json
import csv
import glob
import re
import os

# --- 配置 ---
JSON_FOLDER_PATH = 'data_fangji/*.json'  # 你的JSON文件夹路径
OUT_NODE_FANGJI = 'nodes_prescriptions.csv'
OUT_NODE_HERB = 'nodes_herbs.csv'
OUT_EDGE_COMPOSITION = 'edges_prescription_composition.csv'
OUT_EDGE_TREATS = 'edges_prescription_treats.csv'

# --- 扩展清洗列表：用于去除药材名中的杂质 ---
# 遇到这些词直接从药材名中删掉，保留纯净实体
NOISE_WORDS = ["去皮", "去心", "炙", "炒", "生用", "切", "擘", "打碎", "先煎", "后下", "包煎"]


def clean_title(title, sections):
    """
    清洗方剂标题：
    1. 处理空标题 (325.json)
    2. 去除 '【...】' 或 '出自...' 等杂质 (139.json, 137.json)
    """
    # 1. 如果标题为空，尝试从 sections 获取
    if not title:
        for key in ["方剂名", "方剂出处", "title"]:
            if sections.get(key):
                title = sections.get(key)
                break

    if not title:
        return "未知方剂"

    # 2. 清洗杂质
    # 去除 【...】 及其后面的内容 (如 "四逆加人参汤 【来源】...")
    title = re.sub(r'\s*[【\[\(].*?$', '', title)
    # 去除 "，出自..." (如 "参附汤，出自...")
    title = re.sub(r'，.*$', '', title)
    # 去除空格
    return title.strip()


def clean_herb_name(name):
    """
    清洗药材名：去除括号、特殊字符及预定义的炮制词
    """
    # 去除括号内容 (e.g., "龙骨(煅)")
    name = re.sub(r'[（\(].*?[）\)]', '', name)

    # 去除预定义的噪音词 (e.g., "桂枝去皮" -> "桂枝")
    for word in NOISE_WORDS:
        name = name.replace(word, '')

    # 去除数字和非中文字符（保留纯中文名）
    # 注意：有些药材可能带数字（如一见喜），这里稍微放宽，只去两端的数字
    name = re.sub(r'^\d+|\d+$', '', name)

    return name.strip()


def parse_composition(text):
    """
    超级解析器：处理空格分隔、逗号分隔、各字句、复杂单位
    """
    if not text or text == "暂无数据":
        return []

    results = []

    # --- 步骤1：标准化分隔符 ---
    # 这一步是为了解决 136.json "当归9克 芍药9克" 这种空格分隔的情况
    # 逻辑：如果发现 "数字/单位" 后面紧跟 "中文"，则在中间插入逗号

    # 先把中文逗号、分号、换行 统一为英文逗号
    text = text.replace('，', ',').replace('；', ',').replace('\n', ',').replace('。', '')

    # 预处理：保护括号内的内容不被替换（简化处理，先假设括号内无逗号）

    # 核心正则：将空格替换为逗号，但要小心 "各 5g" 这种情况
    # 简单策略：将连续空格替换为逗号，稍后由正则再次清洗
    text = re.sub(r'\s+', ',', text)

    segments = text.split(',')

    for seg in segments:
        seg = seg.strip()
        if not seg: continue

        # --- 情况 A: 处理 "各" 字句 ---
        if '各' in seg:
            # 从右侧分割 "各"，例如 "龙骨、牡蛎各10g"
            parts = seg.rsplit('各', 1)
            if len(parts) == 2:
                herbs_part = parts[0]
                dosage = parts[1]
                # 拆分药材名（可能由顿号或空格分开）
                herbs = re.split(r'[、\s]+', herbs_part)
                for h in herbs:
                    h_clean = clean_herb_name(h)
                    if h_clean:
                        results.append((h_clean, dosage))
            continue

        # --- 情况 B: 普通格式 ---
        # 尝试拆分 药材名 和 剂量
        # 逻辑：找到第一个非中文（数字、g、克、两、枚）的位置

        # 匹配：前面是中文(允许带括号)，后面是数字或特定单位
        # 针对 "附子三枚" 这种纯中文单位，比较难，这里用正则尝试抓取

        # 1. 尝试找数字开始的位置
        match_digit = re.search(r'\d', seg)

        # 2. 尝试找常见单位词开始的位置（针对 "三两", "三枚"）
        match_unit_cn = re.search(r'[一二三四五六七八九十半]+(两|钱|克|枚|分)', seg)

        split_index = -1
        if match_digit:
            split_index = match_digit.start()
        elif match_unit_cn:
            split_index = match_unit_cn.start()

        if split_index > 0:
            name_part = seg[:split_index]
            dosage_part = seg[split_index:]

            # 清洗名字
            h_clean = clean_herb_name(name_part)
            if h_clean:
                results.append((h_clean, dosage_part))
        else:
            # 没找到剂量，可能整个就是药材名（或者格式太乱）
            # 过滤掉纯数字或太短的垃圾字符
            h_clean = clean_herb_name(seg)
            if len(h_clean) > 0 and len(h_clean) < 10:
                results.append((h_clean, "适量"))  # 默认剂量

    return results


# --- 主程序 ---
def main():
    json_files = glob.glob(JSON_FOLDER_PATH)
    print(f"找到 {len(json_files)} 个方剂文件。")

    # 集合用于去重
    nodes_fangji = []
    nodes_herbs = set()
    rels_composition = []

    # 字段映射
    field_mapping = {
        "source": ["出自", "出处", "来源"],
        "effect": ["功效", "功用"],
        "usage": ["用法", "用法用量"],
        "indications": ["主治", "适应症"],
    }

    processed_count = 0

    for file_path in json_files:
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)

            sections = data.get('sections', {})
            raw_title = data.get('title', '')

            # 1. 清洗标题 (ID)
            fangji_name = clean_title(raw_title, sections)
            if fangji_name == "未知方剂": continue  # 跳过无效数据

            # 2. 提取基础属性
            props = {"name": fangji_name}
            for key, keywords in field_mapping.items():
                val = "暂无数据"
                for kw in keywords:
                    if kw in sections:
                        val = sections[kw]
                        break
                props[key] = str(val).replace('\n', ' ').strip()

            # 存入方剂节点列表
            nodes_fangji.append(props)

            # 3. 解析组成 (核心)
            # 尝试获取 "组成" 或 "配方组成"
            comp_text = sections.get('组成', sections.get('配方组成', ''))

            herb_list = parse_composition(comp_text)

            for herb, dosage in herb_list:
                nodes_herbs.add(herb)
                rels_composition.append([fangji_name, herb, dosage])

            processed_count += 1
            if processed_count % 500 == 0:
                print(f"已处理 {processed_count} ...")

        except Exception as e:
            print(f"Error in {file_path}: {e}")

    # --- 写入 CSV ---

    # 1. 方剂节点
    print(f"写入方剂节点 ({len(nodes_fangji)}个)...")
    with open(OUT_NODE_FANGJI, 'w', encoding='utf-8-sig', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['name', 'source', 'effect', 'usage', 'indications'])
        for item in nodes_fangji:
            writer.writerow([
                item['name'], item['source'], item['effect'],
                item['usage'], item['indications']
            ])

    # 2. 药材节点
    print(f"写入药材节点 ({len(nodes_herbs)}个)...")
    with open(OUT_NODE_HERB, 'w', encoding='utf-8-sig', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['name'])
        for h in nodes_herbs:
            writer.writerow([h])

    # 3. 组成关系
    print(f"写入组成关系 ({len(rels_composition)}条)...")
    with open(OUT_EDGE_COMPOSITION, 'w', encoding='utf-8-sig', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['prescription_name', 'herb_name', 'dosage'])
        writer.writerows(rels_composition)

    print("全部完成！")


if __name__ == "__main__":
    main()