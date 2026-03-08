import pandas as pd
from neo4j import GraphDatabase, exceptions
import os
import sys

# ================= 🚨 配置区域 =================
NEO4J_URI = "bolt://localhost:7687"
NEO4J_USER = "neo4j"
NEO4J_PASSWORD = "12345678"  # <--- ⚠️⚠️⚠️ 请务必修改这里的密码！
# ===============================================

DATA_DIR = r"E:\project\TCM_project1\.venv\clean_data"


class TCMFullImporter:
    def __init__(self, uri, user, password):
        self.driver = None
        try:
            self.driver = GraphDatabase.driver(uri, auth=(user, password))
            self.driver.verify_connectivity()
            print("✅ 数据库连接成功！准备开始全量导入。")
        except exceptions.AuthError:
            print("\n❌❌❌【认证失败】❌❌❌")
            print("请检查代码第 10 行的 NEO4J_PASSWORD。")
            sys.exit(1)
        except Exception as e:
            print(f"\n❌ 连接错误: {e}")
            sys.exit(1)

    def close(self):
        if self.driver: self.driver.close()

    # --- 工具：读取CSV并标准化列名 ---
    def load_csv(self, filename, mapping):
        path = os.path.join(DATA_DIR, filename)
        if not os.path.exists(path):
            print(f"   ⚠️ 文件缺失跳过: {filename}")
            return None
        try:
            df = pd.read_csv(path, encoding='utf-8-sig').fillna("")
        except:
            df = pd.read_csv(path, encoding='gbk').fillna("")

        # 智能匹配列名
        new_cols = {}
        for target, candidates in mapping.items():
            for c in candidates:
                if c in df.columns:
                    new_cols[c] = target;
                    break

        # 只有当映射的列都存在时才重命名，防止报错
        return df.rename(columns=new_cols)

    # --- 工具：批量执行Cypher ---
    def batch_run(self, query, data, desc):
        if data is None or len(data) == 0: return
        data_dict = data.to_dict('records')
        print(f"   Execute: {desc} (共 {len(data_dict)} 条)...")
        with self.driver.session() as session:
            batch_size = 2000
            for i in range(0, len(data_dict), batch_size):
                batch = data_dict[i:i + batch_size]
                try:
                    session.run(query, batch=batch)
                except Exception as e:
                    print(f"      ❌ 写入错误: {e}")

    # ================= 1. 清空数据库 =================
    def clear_db(self):
        print("\n🧹 [Step 0] 清空数据库...")
        with self.driver.session() as session:
            session.run("MATCH (n) DETACH DELETE n")
        print("   ✨ 数据库已干干净净。")

    # ================= 2. 构建治疗桥梁 =================
    def build_bridge(self):
        print("\n🏗️ [Step 1] 构建治疗关系桥梁...")
        # (保持原有的智能匹配逻辑)
        out_file = os.path.join(DATA_DIR, "rel_treatment_final.csv")
        file_dis = os.path.join(DATA_DIR, "nodes_diseases.csv")
        file_pres = os.path.join(DATA_DIR, "node_prescription_final.csv")
        file_herb = os.path.join(DATA_DIR, "node_herb_final.csv")

        if not os.path.exists(file_dis): return

        try:
            df_dis = pd.read_csv(file_dis, encoding='utf-8')
        except:
            df_dis = pd.read_csv(file_dis, encoding='gbk')
        dis_list = set([str(x).strip() for x in df_dis['name'].tolist() if len(str(x)) > 1])

        rels = []
        keywords = ['主治', '功能', '功效', '功能主治', 'indication']

        def scan(fpath, label):
            if not os.path.exists(fpath): return
            try:
                df = pd.read_csv(fpath, encoding='utf-8-sig').fillna("")
            except:
                df = pd.read_csv(fpath, encoding='gbk').fillna("")
            cols = df.columns
            for _, row in df.iterrows():
                name = row.get('名称') or row.get('name') or row.get('药名')
                if not name: continue
                text = ""
                for c in cols:
                    if any(k in c for k in keywords) and pd.notna(row[c]): text += str(row[c])
                for d in dis_list:
                    if d in text: rels.append([name, d, label])

        scan(file_pres, 'Prescription')
        scan(file_herb, 'Herb')

        if rels:
            pd.DataFrame(rels, columns=['source', 'target', 'type']).to_csv(out_file, index=False, encoding='utf-8-sig')
            print(f"   💾 生成桥接文件: {len(rels)} 条关系")

    # ================= 3. 导入所有节点 =================
    def import_nodes(self):
        print("\n📦 [Step 2] 导入所有实体节点...")

        # 1. 方剂
        df = self.load_csv("node_prescription_final.csv", {
            'name': ['名称', 'name'], 'intro': ['方解', 'explanation'],
            'usage': ['用法', 'usage'], 'clinical': ['临床应用'], 'other': ['其他信息']
        })
        if df is not None:
            self.batch_run("""UNWIND $batch AS row MERGE (n:Prescription {name: row.name}) 
                              SET n.方解=row.intro, n.用法=row.usage, n.临床应用=row.clinical""", df, "方剂")

        # 2. 中药
        df = self.load_csv("node_herb_final.csv", {
            'name': ['名称', 'name'], 'flavor': ['性味'], 'part': ['入药部位'], 'other': ['其他信息']
        })
        if df is not None:
            self.batch_run("""UNWIND $batch AS row MERGE (n:Herb {name: row.name}) 
                              SET n.性味=row.flavor, n.入药部位=row.part""", df, "中药")

        # 3. 疾病 (含类别、简介)
        df = self.load_csv("nodes_diseases.csv", {
            'id': ['id'], 'name': ['name', '名称'], 'intro': ['introduction', '简介'],
            'cause': ['cause', '病因'], 'cat': ['category', 'category_name']
        })
        if df is not None:
            # 这里把 category 既作为属性，也可以之后作为节点
            self.batch_run("""UNWIND $batch AS row MERGE (n:Disease {name: row.name}) 
                              SET n.id=toString(row.id), n.简介=row.intro, n.病因=row.cause, n.category=row.cat""", df,
                           "疾病")

        # 4. 症状 & 科室
        df = self.load_csv("nodes_symptoms.csv", {'name': ['name']})
        if df is not None: self.batch_run("UNWIND $batch AS row MERGE (n:Symptom {name: row.name})", df, "症状")

        df = self.load_csv("nodes_departments.csv", {'name': ['name']})
        if df is not None: self.batch_run("UNWIND $batch AS row MERGE (n:Department {name: row.name})", df, "科室")

        # 5. 【关键】导入附属实体 (产地、功效、文献等) - node_entities_final.csv
        df = self.load_csv("node_entities_final.csv", {'name': ['name'], 'label': ['label']})
        if df is not None:
            # 按 label 分组导入，因为 Cypher 不能动态传 label
            groups = df.groupby('label')
            for label, group in groups:
                # 剔除奇怪的 label
                clean_label = str(label).strip().replace(" ", "")
                if not clean_label: continue
                self.batch_run(f"UNWIND $batch AS row MERGE (n:{clean_label} {{name: row.name}})", group,
                               f"实体-{clean_label}")

    # ================= 4. 导入所有关系 =================
    def import_relations(self):
        print("\n🔗 [Step 3] 导入所有关系...")

        # 1. 疾病 -> 症状 (ID匹配)
        df = self.load_csv("edges_disease_symptom.csv", {'did': ['disease_id'], 'sname': ['symptom_name', 'name']})
        if df is not None:
            self.batch_run("""UNWIND $batch AS row MATCH (d:Disease) WHERE d.id = toString(row.did)
                              MATCH (s:Symptom {name: row.sname}) MERGE (d)-[:HAS_SYMPTOM]->(s)""", df, "疾病-症状")

        # 2. 疾病 -> 科室 (ID匹配)
        df = self.load_csv("edges_disease_department.csv",
                           {'did': ['disease_id'], 'dname': ['department_name', 'name']})
        if df is not None:
            self.batch_run("""UNWIND $batch AS row MATCH (d:Disease) WHERE d.id = toString(row.did)
                              MATCH (dep:Department {name: row.dname}) MERGE (d)-[:BELONGS_TO]->(dep)""", df,
                           "疾病-科室")

        # 3. 治疗关系 (方剂/中药 -> 疾病)
        df = self.load_csv("rel_treatment_final.csv", {'src': ['source'], 'tgt': ['target'], 'type': ['type']})
        if df is not None:
            self.batch_run("""UNWIND $batch AS row MATCH (s:Prescription {name: row.src}) 
                              MATCH (t:Disease {name: row.tgt}) MERGE (s)-[:TREATS]->(t)""",
                           df[df['type'] == 'Prescription'], "方剂-治疗-疾病")
            self.batch_run("""UNWIND $batch AS row MATCH (s:Herb {name: row.src}) 
                              MATCH (t:Disease {name: row.tgt}) MERGE (s)-[:TREATS]->(t)""",
                           df[df['type'] == 'Herb'], "中药-治疗-疾病")

        # 4. 【关键】导入复杂中药/方剂关系 (relations_all_final.csv, rel_herb_final.csv)
        # 这里的难点是关系类型和目标类型是动态的

        def process_complex_rels(filename, default_source_label):
            df = self.load_csv(filename, {
                'src': ['source', 'source_prescription', 'source_herb'],
                'tgt': ['target', 'target_entity'],
                'rel': ['relation', 'relation_type'],
                'ttype': ['target_type']
            })
            if df is None or df.empty: return

            # 按 (关系类型, 目标节点类型) 分组处理
            # 例如: group=('COMPOSED_OF', 'Herb')
            if 'ttype' not in df.columns:
                print(f"   ⚠️ {filename} 缺少 target_type 列，跳过复杂关系处理。")
                return

            grouped = df.groupby(['rel', 'ttype'])
            for (rel_type, target_label), group in grouped:
                rel_upper = str(rel_type).upper().strip().replace(" ", "_")
                tgt_label_clean = str(target_label).strip()

                # 构造 Cypher
                # 假设源节点已知 (Prescription 或 Herb)，目标节点也是通过 Name 匹配
                query = f"""
                UNWIND $batch AS row 
                MATCH (s:{default_source_label} {{name: row.src}})
                MATCH (t:{tgt_label_clean} {{name: row.tgt}})
                MERGE (s)-[:{rel_upper}]->(t)
                """
                self.batch_run(query, group, f"{default_source_label}-[{rel_upper}]->{tgt_label_clean}")

        # 处理方剂的复杂关系 (组成、文献等)
        process_complex_rels("relations_all_final.csv", "Prescription")

        # 处理中药的复杂关系 (归经、产地、功效等)
        process_complex_rels("rel_herb_final.csv", "Herb")


if __name__ == "__main__":
    importer = TCMFullImporter(NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD)

    importer.clear_db()  # 1. 清空
    importer.build_bridge()  # 2. 构建治疗关系
    importer.import_nodes()  # 3. 导入所有节点（含实体）
    importer.import_relations()  # 4. 导入所有关系（含复杂关系）

    importer.close()
    print("\n🎉🎉🎉 终极全量导入完成！")
    print("现在包含了：科室、症状、方剂组成、中药归经、产地、文献、功效等所有数据。")