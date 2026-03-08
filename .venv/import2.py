import pandas as pd
import os

# ================= 配置 =================
BASE_DIR = r"E:\project\TCM_project1\.venv\clean_data"

# 输入文件
FILE_PRESCRIPTION = os.path.join(BASE_DIR, "node_prescription_final.csv")
FILE_HERB = os.path.join(BASE_DIR, "node_herb_final.csv")
FILE_DISEASE = os.path.join(BASE_DIR, "nodes_diseases.csv")

# 输出文件
OUTPUT_REL = os.path.join(BASE_DIR, "rel_treatment_final.csv")


def get_indication_text(row, columns):
    """
    智能获取主治文本：扫描所有可能的列名
    """
    text = ""
    # 可能包含病症信息的列名列表
    target_cols = ['主治', '功能', '功效', '功能主治', 'indication', 'efficacy']

    for col in target_cols:
        if col in columns:
            val = row[col]
            if pd.notna(val):
                text += str(val) + " "
    return text


def generate_bridge():
    print("🚀 开始构建 [方剂/中药 -> 疾病] 的桥接关系...")

    # 1. 读取疾病库
    if not os.path.exists(FILE_DISEASE):
        print("❌ 找不到疾病文件 nodes_diseases.csv，无法构建。")
        return

    try:
        df_dis = pd.read_csv(FILE_DISEASE, encoding='utf-8')
    except:
        df_dis = pd.read_csv(FILE_DISEASE, encoding='gbk')

    # 制作疾病关键词列表 (过滤掉单字，比如“痛”、“咳”，避免匹配太泛)
    disease_list = [str(x).strip() for x in df_dis['name'].tolist() if len(str(x)) > 1]
    disease_list = list(set(disease_list))  # 去重
    print(f"📋 加载了 {len(disease_list)} 个疾病关键词。")

    relationships = []

    # 2. 扫描方剂 (Prescription)
    if os.path.exists(FILE_PRESCRIPTION):
        print("🔍 正在扫描方剂主治...")
        try:
            df_pres = pd.read_csv(FILE_PRESCRIPTION).fillna("")
        except:
            df_pres = pd.read_csv(FILE_PRESCRIPTION, encoding='gbk').fillna("")

        count = 0
        cols = df_pres.columns.tolist()  # 获取实际列名

        for _, row in df_pres.iterrows():
            # 获取名字
            p_name = row.get('名称') or row.get('name')
            if not p_name: continue

            # 智能获取主治文本
            indication = get_indication_text(row, cols)

            # 关键词匹配
            for dis in disease_list:
                if dis in indication:
                    relationships.append([p_name, dis, 'Prescription'])

            count += 1
            if count % 200 == 0: print(f"   已扫描 {count} 个方剂...", end='\r')
        print(f"   方剂扫描完成，找到 {len(relationships)} 条潜在关系。")

    # 3. 扫描中药 (Herb)
    if os.path.exists(FILE_HERB):
        print("🔍 正在扫描中药主治...")
        try:
            df_herb = pd.read_csv(FILE_HERB).fillna("")
        except:
            df_herb = pd.read_csv(FILE_HERB, encoding='gbk').fillna("")

        herb_count = 0
        cols = df_herb.columns.tolist()  # 获取实际列名
        initial_rel_count = len(relationships)

        for _, row in df_herb.iterrows():
            # 获取名字
            h_name = row.get('名称') or row.get('name') or row.get('药名')
            if not h_name: continue

            # 智能获取主治文本 (这里就不会报错了，因为它只读存在的列)
            indication = get_indication_text(row, cols)

            for dis in disease_list:
                if dis in indication:
                    relationships.append([h_name, dis, 'Herb'])

            herb_count += 1
            if herb_count % 200 == 0: print(f"   已扫描 {herb_count} 个中药...", end='\r')

        print(f"   中药扫描完成，新增 {len(relationships) - initial_rel_count} 条关系。")

    # 4. 保存结果
    if relationships:
        df_out = pd.DataFrame(relationships, columns=['source', 'target', 'type'])
        df_out.to_csv(OUTPUT_REL, index=False, encoding='utf-8-sig')
        print(f"\n✅ 成功生成桥接文件！共 {len(relationships)} 条。")
        print(f"📂 文件保存在: {OUTPUT_REL}")
        print("👉 下一步：请再次运行 import_final_v3.py 将这些关系导入 Neo4j。")
    else:
        print("\n⚠️ 未匹配到任何关系，可能是疾病名称和主治描述差异太大。")


if __name__ == "__main__":
    generate_bridge()