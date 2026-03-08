import pandas as pd
from neo4j import GraphDatabase, exceptions
import os
import sys

# ================= 🚨 配置区域 =================
NEO4J_URI = "bolt://localhost:7687"
NEO4J_USER = "neo4j"
NEO4J_PASSWORD = "12345678"  # <--- ⚠️ 改成你的真实密码
# ===============================================

DATA_DIR = r"E:\project\TCM_project1\.venv\clean_data"


class TCMFinalImporter:
    def __init__(self, uri, user, password):
        self.driver = None
        try:
            self.driver = GraphDatabase.driver(uri, auth=(user, password))
            self.driver.verify_connectivity()
            print("✅ 数据库连接成功！开始执行终极导入...")
        except exceptions.AuthError:
            print("❌ 密码错误，请检查代码第 10 行。")
            sys.exit(1)
        except Exception as e:
            print(f"❌ 连接错误: {e}")
            sys.exit(1)

    def close(self):
        if self.driver: self.driver.close()

    def load_csv(self, filename, mapping=None):
        path = os.path.join(DATA_DIR, filename)
        if not os.path.exists(path):
            print(f"   ⚠️ 文件缺失: {filename}")
            return None
        try:
            df = pd.read_csv(path, encoding='utf-8-sig').fillna("")
        except:
            df = pd.read_csv(path, encoding='gbk').fillna("")

        # 如果提供了映射字典，重命名列
        if mapping:
            new_cols = {}
            for target, candidates in mapping.items():
                for c in candidates:
                    if c in df.columns:
                        new_cols[c] = target;
                        break
            df = df.rename(columns=new_cols)
        return df

    def batch_run(self, query, data, desc):
        if data is None or len(data) == 0: return
        data_dict = data.to_dict('records')
        print(f"   Execute: {desc} ({len(data_dict)} 条)...")
        with self.driver.session() as session:
            batch_size = 2000
            for i in range(0, len(data_dict), batch_size):
                batch = data_dict[i:i + batch_size]
                try:
                    session.run(query, batch=batch)
                except Exception as e:
                    print(f"      ❌ 写入错误: {e}")

    # --- 1. 清空 ---
    def clear_db(self):
        print("\n🧹 [Step 0] 清空数据库...")
        with self.driver.session() as session:
            session.run("MATCH (n) DETACH DELETE n")

    # --- 2. 治疗关系生成 (构建桥梁) ---
    def build_bridge(self):
        print("\n🏗️ [Step 1] 重新生成治疗关系...")
        out_file = "rel_treatment_final.csv"
        # 简单逻辑：如果文件不存在或为了确保最新，可以在内存里生成，这里简化为读取
        # 为了保证完整性，我们还是用之前的逻辑快速扫一遍，防止文件缺失
        # 但如果你的文件已经齐全，这一步可以跳过。这里为了保险保留。
        file_dis = os.path.join(DATA_DIR, "nodes_diseases.csv")
        file_pres = os.path.join(DATA_DIR, "node_prescription_final.csv")
        file_herb = os.path.join(DATA_DIR, "node_herb_final.csv")

        if not os.path.exists(file_dis): return
        # (此处省略复杂的文本匹配代码，直接假设你已经有 rel_treatment_final.csv)
        # 如果没有这个文件，请告诉我，我再加上生成代码。
        pass

        # --- 3. 导入节点 ---

    def import_nodes(self):
        print("\n📦 [Step 2] 导入实体节点...")

        # 1. 方剂
        df = self.load_csv("node_prescription_final.csv",
                           {'name': ['name', '名称'], 'intro': ['方解'], 'usage': ['用法'], 'clinical': ['临床应用']})
        if df is not None:
            self.batch_run(
                "UNWIND $batch AS row MERGE (n:Prescription {name: row.name}) SET n.方解=row.intro, n.用法=row.usage, n.临床应用=row.clinical",
                df, "方剂")

        # 2. 中药
        df = self.load_csv("node_herb_final.csv", {'name': ['name', '名称'], 'flavor': ['性味'], 'part': ['入药部位']})
        if df is not None:
            self.batch_run(
                "UNWIND $batch AS row MERGE (n:Herb {name: row.name}) SET n.性味=row.flavor, n.入药部位=row.part", df,
                "中药")

        # 3. 疾病
        df = self.load_csv("nodes_diseases.csv",
                           {'id': ['id'], 'name': ['name'], 'intro': ['introduction'], 'cat': ['category']})
        if df is not None:
            self.batch_run(
                "UNWIND $batch AS row MERGE (n:Disease {name: row.name}) SET n.id=toString(row.id), n.简介=row.intro, n.category=row.cat",
                df, "疾病")

        # 4. 症状 & 科室
        df = self.load_csv("nodes_symptoms.csv", {'name': ['name']})
        if df is not None: self.batch_run("UNWIND $batch AS row MERGE (n:Symptom {name: row.name})", df, "症状")

        df = self.load_csv("nodes_departments.csv", {'name': ['name']})
        if df is not None: self.batch_run("UNWIND $batch AS row MERGE (n:Department {name: row.name})", df, "科室")

        # 5. 附属实体 (Origin, Literature, Category...)
        df = self.load_csv("node_entities_final.csv", {'name': ['name'], 'label': ['label']})
        if df is not None:
            groups = df.groupby('label')
            for label, group in groups:
                clean_label = str(label).strip().replace(" ", "")
                if clean_label:
                    self.batch_run(f"UNWIND $batch AS row MERGE (n:{clean_label} {{name: row.name}})", group,
                                   f"实体-{clean_label}")

    # --- 4. 导入关系 (含剂量！) ---
    def import_relations(self):
        print("\n🔗 [Step 3] 导入关系...")

        # 1. 基础关系
        df = self.load_csv("edges_disease_symptom.csv", {'did': ['disease_id'], 'sname': ['symptom_name']})
        if df is not None:
            self.batch_run(
                "UNWIND $batch AS row MATCH (d:Disease {id: toString(row.did)}), (s:Symptom {name: row.sname}) MERGE (d)-[:HAS_SYMPTOM]->(s)",
                df, "疾病-症状")

        df = self.load_csv("edges_disease_department.csv", {'did': ['disease_id'], 'dname': ['department_name']})
        if df is not None:
            self.batch_run(
                "UNWIND $batch AS row MATCH (d:Disease {id: toString(row.did)}), (dep:Department {name: row.dname}) MERGE (d)-[:BELONGS_TO]->(dep)",
                df, "疾病-科室")

        # 2. 治疗关系 (如果你没有 rel_treatment_final.csv，这部分会跳过)
        df = self.load_csv("rel_treatment_final.csv", {'src': ['source'], 'tgt': ['target'], 'type': ['type']})
        if df is not None:
            self.batch_run(
                "UNWIND $batch AS row MATCH (p:Prescription {name: row.src}), (d:Disease {name: row.tgt}) MERGE (p)-[:TREATS]->(d)",
                df[df['type'] == 'Prescription'], "方剂-治疗")
            self.batch_run(
                "UNWIND $batch AS row MATCH (h:Herb {name: row.src}), (d:Disease {name: row.tgt}) MERGE (h)-[:TREATS]->(d)",
                df[df['type'] == 'Herb'], "中药-治疗")

        # ==========================================================
        # 🌟 重点修正：导入 relations_all_final.csv (含剂量)
        # ==========================================================
        # 映射列名：src=source_prescription, tgt=target_entity, rel=relation_type, prop=property
        df_complex = self.load_csv("relations_all_final.csv", {
            'src': ['source_prescription'],
            'tgt': ['target_entity'],
            'rel': ['relation_type'],
            'ttype': ['target_type'],
            'prop': ['property']  # <--- 这里就是剂量！
        })

        if df_complex is not None:
            # 1. 处理组成关系 (composition) -> 写入 dosage 属性
            df_comp = df_complex[df_complex['rel'] == 'composition']
            # 注意：这里的 tgt 是 Herb
            query_comp = """
            UNWIND $batch AS row
            MATCH (p:Prescription {name: row.src})
            MATCH (h:Herb {name: row.tgt})
            MERGE (p)-[r:COMPOSED_OF]->(h)
            SET r.dosage = row.prop  // <--- 将 CSV 里的 property 列写入 dosage 属性
            """
            self.batch_run(query_comp, df_comp, "方剂组成(含剂量)")

            # 2. 处理文献出处 (source)
            df_src = df_complex[df_complex['rel'] == 'source']
            query_src = """
            UNWIND $batch AS row
            MATCH (p:Prescription {name: row.src})
            MERGE (l:Literature {name: row.tgt})
            MERGE (p)-[:HAS_SOURCE]->(l)
            """
            self.batch_run(query_src, df_src, "方剂出处")

            # 3. 处理分类 (category)
            df_cat = df_complex[df_complex['rel'] == 'category']
            query_cat = """
            UNWIND $batch AS row
            MATCH (p:Prescription {name: row.src})
            MERGE (c:Category {name: row.tgt})
            MERGE (p)-[:HAS_CATEGORY]->(c)
            """
            self.batch_run(query_cat, df_cat, "方剂分类")

        # ==========================================================
        # 处理 rel_herb_final.csv (中药的归经、产地等)
        # ==========================================================
        df_herb_rel = self.load_csv("rel_herb_final.csv", {
            'src': ['source_herb', 'source'], 'tgt': ['target_entity', 'target'], 'rel': ['relation_type', 'relation']
        })
        if df_herb_rel is not None:
            # 简单处理：将 rel 列转为大写作为关系类型
            # 例如: origin -> HAS_ORIGIN, flavor -> HAS_FLAVOR
            # 也可以根据你的 csv 内容做 specific mapping

            # 这里演示处理 "origin" -> HAS_ORIGIN
            df_origin = df_herb_rel[df_herb_rel['rel'].str.contains('origin|产地', case=False, na=False)]
            if not df_origin.empty:
                self.batch_run(
                    """UNWIND $batch AS row MATCH (h:Herb {name: row.src}) MERGE (o:Origin {name: row.tgt}) MERGE (h)-[:HAS_ORIGIN]->(o)""",
                    df_origin, "中药产地")

            # 处理 "flavor" -> HAS_FLAVOR
            df_flavor = df_herb_rel[df_herb_rel['rel'].str.contains('flavor|性味', case=False, na=False)]
            if not df_flavor.empty:
                self.batch_run(
                    """UNWIND $batch AS row MATCH (h:Herb {name: row.src}) MERGE (f:Flavor {name: row.tgt}) MERGE (h)-[:HAS_FLAVOR]->(f)""",
                    df_flavor, "中药性味")


if __name__ == "__main__":
    importer = TCMFinalImporter(NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD)
    importer.clear_db()
    importer.build_bridge()
    importer.import_nodes()
    importer.import_relations()
    importer.close()
    print("\n🎉🎉🎉 全部完成！")
    print("现在关系上不仅有连线，方剂的组成关系上还有【剂量】了！")