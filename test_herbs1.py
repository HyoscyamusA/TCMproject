import json
import os
import glob
import re
import csv

# ================= ⚙️ 配置区域 =================

INPUT_DIR = r"E:\project\TCM_project1\.venv\data_herbs"

# 输出路径
BASE_OUT_DIR = r"E:\project\TCM_project1\.venv\clean_data"
OUTPUT_HERB_NODE = os.path.join(BASE_OUT_DIR, "node_herb_final.csv")  # 药材主节点
OUTPUT_REL = os.path.join(BASE_OUT_DIR, "rel_herb_final.csv")  # 关系
OUTPUT_OTHER_NODES = os.path.join(BASE_OUT_DIR, "node_entities_final.csv")  # 附属节点(功效/产地等)

# ================= 🔧 提取逻辑 =================

ATTRIBUTE_MAPPING = {
    "名称": ["中药名", "title", "名称"],
    "性味": ["性味归经", "性味", "性味与归经"],
    "入药部位": ["药用部位", "入药部位", "药用"],
    "别名": ["别名"],
    "主治": ["主治", "功能主治"],
    "文献": ["文献", "文献论述", "各家论述"],
    "用法": ["用法用量", "用法", "临床应用"],
    "配伍选方": ["配伍选方", "选方", "附方", "相关配伍"]
}


def extract_entities_and_relations(herb_name, sections):
    """
    返回两个列表：
    1. relations: [source, target, target_type, relation]
    2. entities: [name, label]  <- 用来生成附属节点
    """
    edges = []
    new_nodes = []

    # 1. 提取【功效】
    eff_keys = ["功效", "功效与作用", "功能", "功效作用"]
    for k in eff_keys:
        if k in sections:
            # 清洗逻辑：按逗号分号拆分，去掉句号
            text = sections[k].replace("。", "").replace("；", "，").replace(";", "，")
            parts = text.split("，")
            for p in parts:
                p = p.strip()
                if 1 < len(p) < 15:  # 长度限制，太长不像是一个具体的功效词
                    edges.append([herb_name, p, "Efficacy", "has_efficacy"])
                    new_nodes.append([p, "Efficacy"])
            break

    # 2. 提取【类别】
    for k in ["类别", "分类"]:
        if k in sections:
            cat = sections[k].replace("。", "").strip()
            if cat:
                edges.append([herb_name, cat, "Category", "belongs_to"])
                new_nodes.append([cat, "Category"])
            break

    # 3. 提取【产地】
    # 规则优化：提取 "分布于" 后面的内容，按逗号切割
    for k in ["产地分布", "主要产地", "产地"]:
        if k in sections:
            text = sections[k]
            # 尝试正则提取地点
            locs = []
            if "分布于" in text:
                sub = text.split("分布于")[-1].split("。")[0]
                locs = re.split(r'[，、]', sub)
            else:
                # 简单兜底，取前20个字里的地名（这里简化处理，直接取整句如果短的话）
                if len(text) < 10: locs = [text]

            for loc in locs:
                loc = loc.strip()
                if 1 < len(loc) < 10:
                    edges.append([herb_name, loc, "Origin", "produced_in"])
                    new_nodes.append([loc, "Origin"])
            break

    # 4. 提取【文献书名】
    # 扫描整个 sections 内容找书名号
    full_str = str(sections)
    books = re.findall(r'《(.*?)》', full_str)
    for book in set(books):
        if len(book) > 1 and len(book) < 20:
            edges.append([herb_name, book, "Literature", "cited_by"])
            new_nodes.append([book, "Literature"])

    # 5. 提取【禁忌】
    # 禁忌通常是一句话，这里作为节点处理
    for k in ["使用禁忌", "禁忌", "注意事项"]:
        if k in sections:
            txt = sections[k].strip()
            if txt and len(txt) < 50:  # 太长的就不做节点了，做属性即可
                # 去掉多余标点
                txt = txt.replace("。", "")
                edges.append([herb_name, txt, "Contraindication", "has_contraindication"])
                new_nodes.append([txt, "Contraindication"])
            break

    return edges, new_nodes


# ================= ▶️ 主程序 =================

def process_files():
    if not os.path.exists(INPUT_DIR):
        print("❌ 文件夹不存在")
        return

    os.makedirs(BASE_OUT_DIR, exist_ok=True)
    json_files = glob.glob(os.path.join(INPUT_DIR, "*.json"))
    print(f"🚀 开始处理 {len(json_files)} 个药材文件...")

    # 用于去重附属节点 (避免重复写入同一个“解表药”节点)
    # 格式: set("实体名|类型")
    seen_entities = set()

    with open(OUTPUT_HERB_NODE, 'w', newline='', encoding='utf-8-sig') as f_herb, \
            open(OUTPUT_REL, 'w', newline='', encoding='utf-8-sig') as f_rel, \
            open(OUTPUT_OTHER_NODES, 'w', newline='', encoding='utf-8-sig') as f_others:

        # 1. 写 Herb 节点表头
        herb_cols = list(ATTRIBUTE_MAPPING.keys()) + ["其他信息"]
        herb_writer = csv.DictWriter(f_herb, fieldnames=herb_cols)
        herb_writer.writeheader()

        # 2. 写 关系 表头
        rel_writer = csv.writer(f_rel)
        rel_writer.writerow(['source', 'target', 'target_type', 'relation'])

        # 3. 写 附属节点 表头
        other_writer = csv.writer(f_others)
        other_writer.writerow(['name', 'label'])  # label 即节点类型 (Efficacy, Category等)

        count = 0
        for json_path in json_files:
            try:
                with open(json_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
            except:
                continue

            items = data if isinstance(data, list) else [data]

            for item in items:
                count += 1
                sections = item.get('sections', {})
                if not sections:
                    sections = {k: v for k, v in item.items() if k != 'sections'}

                # --- A. 确定药材名 (ID) ---
                name = sections.get("中药名") or sections.get("title") or sections.get("名称")
                if not name: name = os.path.basename(json_path).replace('.json', '')

                # --- B. 写入药材节点 (Node: Herb) ---
                node_row = {}
                for target, keys in ATTRIBUTE_MAPPING.items():
                    val = ""
                    for k in keys:
                        if sections.get(k):
                            val = str(sections[k])
                            break
                    node_row[target] = val

                # 其他信息
                others_dict = {k: v for k, v in sections.items() if k not in sum(ATTRIBUTE_MAPPING.values(), [])}
                node_row["其他信息"] = json.dumps(others_dict, ensure_ascii=False)
                node_row["名称"] = name  # 确保ID存在
                herb_writer.writerow(node_row)

                # --- C. 提取并写入 关系 & 附属节点 ---
                edges, new_entities = extract_entities_and_relations(name, sections)

                # 写入关系
                for e in edges:
                    rel_writer.writerow(e)

                # 写入附属节点 (去重)
                for ent_name, ent_label in new_entities:
                    unique_key = f"{ent_name}|{ent_label}"
                    if unique_key not in seen_entities:
                        other_writer.writerow([ent_name, ent_label])
                        seen_entities.add(unique_key)

                if count % 100 == 0: print(f"已处理 {count} 条...")

    print(f"\n✅ 全部完成！生成了三个文件：")
    print(f"1. 药材节点: {OUTPUT_HERB_NODE}")
    print(f"2. 关系表:   {OUTPUT_REL}")
    print(f"3. 实体节点: {OUTPUT_OTHER_NODES} (包含功效、产地、文献等)")


if __name__ == "__main__":
    process_files()