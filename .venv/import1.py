import pandas as pd
from neo4j import GraphDatabase
import os

# ================= ⚙️ 配置区域 =================

# 1. Neo4j 连接 (修改密码)
URI = "bolt://localhost:7687"
AUTH = ("neo4j", "12345678")

# 2. 数据文件夹路径 (确保所有CSV都在这里)
DATA_DIR = r"E:\project\TCM_project1\.venv\clean_data"

# ================= 📂 文件映射 =================
# 中药/方剂数据
FILE_NODE_PRESCRIPTION = os.path.join(DATA_DIR, "node_prescription_final.csv")
FILE_NODE_HERB = os.path.join(DATA_DIR, "node_herb_final.csv")
FILE_NODE_ENTITIES = os.path.join(DATA_DIR, "node_entities_final.csv")  # 功效/产地/文献等
FILE_REL_PRESCRIPTION = os.path.join(DATA_DIR, "relations_all_final.csv")  # 方剂的组成/来源关系
FILE_REL_HERB = os.path.join(DATA_DIR, "rel_herb_final.csv")  # 药材的各类关系

# 疾病库数据 (XYWY)
FILE_NODE_DISEASE = os.path.join(DATA_DIR, "nodes_diseases.csv")
FILE_NODE_SYMPTOM = os.path.join(DATA_DIR, "nodes_symptoms.csv")
FILE_NODE_DEPT = os.path.join(DATA_DIR, "nodes_departments.csv")
FILE_EDGE_DIS_SYM = os.path.join(DATA_DIR, "edges_disease_symptom.csv")
FILE_EDGE_DIS_DEPT = os.path.join(DATA_DIR, "edges_disease_department.csv")

# 桥接数据 (从方剂主治中提取出的治疗关系)
FILE_REL_TREAT = os.path.join(DATA_DIR, "rel_treatment_final.csv")


# ================= 🔧 导入引擎 =================

class FullImporter:
    def __init__(self, uri, auth):
        self.driver = GraphDatabase.driver(uri, auth=auth)

    def close(self):
        self.driver.close()

    def run_query(self, query, parameters=None):
        with self.driver.session() as session:
            session.run(query, parameters)

    def _batch_run(self, query, df, batch_size=2000):
        """通用分批执行器"""
        data = df.to_dict('records')
        with self.driver.session() as session:
            for i in range(0, len(data), batch_size):
                batch = data[i:i + batch_size]
                try:
                    session.run(query, batch=batch)
                except Exception as e:
                    print(f"⚠️ 批次写入警告: {e}")
        print(f"   -> 写入 {len(data)} 条。")

    # --- 1. 基础设施 ---
    def setup_database(self):
        print("🛠️ 正在初始化数据库索引 (Constraints)...")
        # 为所有节点类型创建唯一索引 (Name)，这对于 MERGE 至关重要
        labels = [
            "Prescription", "Herb", "Disease", "Symptom", "Department",
            "Efficacy", "Category", "Origin", "Source", "Literature", "Contraindication"
        ]
        for label in labels:
            q = f"CREATE CONSTRAINT IF NOT EXISTS FOR (n:{label}) REQUIRE n.name IS UNIQUE"
            self.run_query(q)
        print("✅ 索引创建完成")

    # --- 2. 导入节点 ---
    def import_nodes(self):
        print("\n📦 === 开始导入节点 ===")

        # 2.1 附属实体 (功效、产地等)
        if os.path.exists(FILE_NODE_ENTITIES):
            print("正在导入附属实体 (Category/Efficacy/Origin)...")
            df = pd.read_csv(FILE_NODE_ENTITIES).fillna("")
            # 按 label 分组导入
            for label, group in df.groupby('label'):
                query = f"UNWIND $batch AS row MERGE (n:{label} {{name: row.name}})"
                self._batch_run(query, group)

        # 2.2 症状 & 科室
        if os.path.exists(FILE_NODE_SYMPTOM):
            print("正在导入症状 (Symptom)...")
            df = pd.read_csv(FILE_NODE_SYMPTOM).fillna("")
            self._batch_run("UNWIND $batch AS row MERGE (s:Symptom {name: row.name})", df)

        if os.path.exists(FILE_NODE_DEPT):
            print("正在导入科室 (Department)...")
            df = pd.read_csv(FILE_NODE_DEPT).fillna("")
            self._batch_run("UNWIND $batch AS row MERGE (d:Department {name: row.name})", df)

        # 2.3 疾病 (Disease) - 包含详细属性
        if os.path.exists(FILE_NODE_DISEASE):
            print("正在导入疾病详情 (Disease)...")
            df = pd.read_csv(FILE_NODE_DISEASE).fillna("")
            query = """
            UNWIND $batch AS row
            MERGE (d:Disease {name: row.name})
            SET d.introduction = row.introduction,
                d.cause = row.cause,
                d.prevention = row.prevention,
                d.category = row.category
            """
            self._batch_run(query, df)

        # 2.4 药材 (Herb)
        if os.path.exists(FILE_NODE_HERB):
            print("正在导入药材 (Herb)...")
            df = pd.read_csv(FILE_NODE_HERB).fillna("")
            query = """
            UNWIND $batch AS row
            MERGE (h:Herb {name: row.名称})
            SET h.性味 = row.性味, h.入药部位 = row.入药部位, h.主治 = row.主治
            """
            self._batch_run(query, df)

        # 2.5 方剂 (Prescription)
        if os.path.exists(FILE_NODE_PRESCRIPTION):
            print("正在导入方剂 (Prescription)...")
            df = pd.read_csv(FILE_NODE_PRESCRIPTION).fillna("")
            query = """
            UNWIND $batch AS row
            MERGE (p:Prescription {name: row.名称})
            SET p.功效 = row.功效, p.主治 = row.主治, p.组成 = row.组成原文, p.用法 = row.用法用量
            """
            self._batch_run(query, df)

    # --- 3. 导入关系 ---
    def import_relationships(self):
        print("\n🔗 === 开始导入关系 ===")

        # 3.1 疾病内部关系 (疾病-症状, 疾病-科室)
        if os.path.exists(FILE_EDGE_DIS_SYM):
            print("连接: 疾病 -> 症状 ...")
            df = pd.read_csv(FILE_EDGE_DIS_SYM).fillna("")
            query = """
            UNWIND $batch AS row
            MATCH (d:Disease {name: row.disease})
            MATCH (s:Symptom {name: row.symptom})
            MERGE (d)-[:HAS_SYMPTOM]->(s)
            """
            self._batch_run(query, df)

        if os.path.exists(FILE_EDGE_DIS_DEPT):
            print("连接: 疾病 -> 科室 ...")
            df = pd.read_csv(FILE_EDGE_DIS_DEPT).fillna("")
            query = """
            UNWIND $batch AS row
            MATCH (d:Disease {name: row.disease})
            MATCH (dep:Department {name: row.department})
            MERGE (d)-[:BELONGS_TO]->(dep)
            """
            self._batch_run(query, df)

        # 3.2 药材/方剂的属性关系 (组成、产地、类别等)
        # 统一处理 relations_all_final.csv 和 rel_herb_final.csv
        for file_path, source_label in [(FILE_REL_PRESCRIPTION, "Prescription"), (FILE_REL_HERB, "Herb")]:
            if not os.path.exists(file_path): continue
            print(f"连接: {source_label} 的属性关系 ({os.path.basename(file_path)})...")

            df = pd.read_csv(file_path).fillna("")

            # 统一列名
            if 'source_prescription' in df.columns:
                df = df.rename(
                    columns={'source_prescription': 'source', 'target_entity': 'target', 'relation_type': 'relation'})
            elif 'source_herb' in df.columns:
                df = df.rename(
                    columns={'source_herb': 'source', 'target_entity': 'target', 'relation_type': 'relation'})

            # 按关系类型分组处理
            for (rel_type, target_type), group in df.groupby(['relation', 'target_type']):
                rel_upper = str(rel_type).upper().replace(" ", "_")
                # 针对组成关系，带剂量属性
                prop_clause = "SET r.dosage = row.property" if 'property' in group.columns else ""

                query = f"""
                UNWIND $batch AS row
                MATCH (s:{source_label} {{name: row.source}})
                MERGE (t:{target_type} {{name: row.target}})
                MERGE (s)-[r:{rel_upper}]->(t)
                {prop_clause}
                """
                self._batch_run(query, group)

        # 3.3 核心桥接：治疗关系 (方剂/药材 -> 疾病)
        # 这是一个关键步骤，它把 TCM 数据和 XYWY 数据连起来
        if os.path.exists(FILE_REL_TREAT):
            print("连接: 方剂/药材 -> [治疗] -> 疾病 ...")
            df = pd.read_csv(FILE_REL_TREAT).fillna("")

            # 分别尝试匹配方剂和药材
            # 1. 方剂治疗疾病
            q_p = """
            UNWIND $batch AS row
            MATCH (p:Prescription {name: row.source})
            MERGE (d:Disease {name: row.target})
            MERGE (p)-[:TREATS]->(d)
            """
            self._batch_run(q_p, df)

            # 2. 药材治疗疾病
            q_h = """
            UNWIND $batch AS row
            MATCH (h:Herb {name: row.source})
            MERGE (d:Disease {name: row.target})
            MERGE (h)-[:TREATS]->(d)
            """
            self._batch_run(q_h, df)


# ================= ▶️ 主程序 =================

if __name__ == "__main__":
    importer = FullImporter(URI, AUTH)
    try:
        # 1. 建立索引 (重要！)
        importer.setup_database()

        # 2. 导入节点
        importer.import_nodes()

        # 3. 导入关系
        importer.import_relationships()

        print("\n🎉🎉🎉 恭喜！全量知识图谱导入完成！")
        print(
            "现在你可以查询：MATCH (p:Prescription)-[:TREATS]->(d:Disease)-[:HAS_SYMPTOM]->(s:Symptom) RETURN p,d,s LIMIT 20")

    except Exception as e:
        print(f"❌ 发生错误: {e}")
    finally:
        importer.close()