import pandas as pd
from neo4j import GraphDatabase, exceptions
import os
import sys
import time

# ================= 🚨配置区域 (请修改密码) =================
NEO4J_URI = "bolt://localhost:7687"
NEO4J_USER = "neo4j"
NEO4J_PASSWORD = "12345678"  # <--- ⚠️ 把这里改成你的真实密码 (例如 "123456")
# ==========================================================

DATA_DIR = r"E:\project\TCM_project1\.venv\clean_data"


class TCMGraphBuilder:
    def __init__(self, uri, user, password):
        self.driver = None
        try:
            self.driver = GraphDatabase.driver(uri, auth=(user, password))
            self.driver.verify_connectivity()  # 立即验证连接
            print("✅ 数据库连接成功！密码正确。")
        except exceptions.AuthError:
            print("\n❌❌❌【认证失败】❌❌❌")
            print("请检查代码第 9 行的 NEO4J_PASSWORD。")
            print("你填写的密码不正确，数据库拒绝访问。")
            sys.exit(1)
        except Exception as e:
            print(f"\n❌ 连接错误: {e}")
            sys.exit(1)

    def close(self):
        if self.driver:
            self.driver.close()

    # --- 1. 核心功能：清空数据库 ---
    def clear_database(self):
        print("\n🧹 [Step 0] 正在清空数据库 (删除所有节点和关系)...")
        with self.driver.session() as session:
            # 这种写法最彻底
            session.run("MATCH (n) DETACH DELETE n")
        print("   ✨ 数据库已清空，准备写入新数据。")

    # --- 2. 工具：通用CSV读取 ---
    def load_csv(self, filename, mapping):
        path = os.path.join(DATA_DIR, filename)
        if not os.path.exists(path):
            print(f"   ⚠️ 文件不存在: {filename}")
            return None
        try:
            df = pd.read_csv(path, encoding='utf-8-sig').fillna("")
        except:
            df = pd.read_csv(path, encoding='gbk').fillna("")

        # 统一列名
        new_cols = {}
        for target, candidates in mapping.items():
            for c in candidates:
                if c in df.columns:
                    new_cols[c] = target;
                    break
        return df.rename(columns=new_cols).to_dict('records')

    # --- 3. 工具：批量写入 ---
    def batch_run(self, query, data, desc):
        if not data: return
        print(f"   Execute: {desc} (共 {len(data)} 条)...")
        with self.driver.session() as session:
            batch_size = 2000
            for i in range(0, len(data), batch_size):
                batch = data[i:i + batch_size]
                try:
                    session.run(query, batch=batch)
                except Exception as e:
                    print(f"      ❌ 写入错误: {e}")

    # --- 4. 业务逻辑：构建治疗关系文件 ---
    def build_treatment_bridge(self):
        print("\n🚀 [Step 1] 构建 [方剂/中药 -> 疾病] 关系文件...")
        out_file = os.path.join(DATA_DIR, "rel_treatment_final.csv")

        # 即使文件存在，为了保险也重新生成一遍
        file_dis = os.path.join(DATA_DIR, "nodes_diseases.csv")
        file_pres = os.path.join(DATA_DIR, "node_prescription_final.csv")
        file_herb = os.path.join(DATA_DIR, "node_herb_final.csv")

        if not os.path.exists(file_dis): return

        # 读取疾病名
        try:
            df_dis = pd.read_csv(file_dis, encoding='utf-8')
        except:
            df_dis = pd.read_csv(file_dis, encoding='gbk')
        disease_list = [str(x).strip() for x in df_dis['name'].tolist() if len(str(x)) > 1]
        disease_list = list(set(disease_list))

        relationships = []
        keywords = ['主治', '功能', '功效', '功能主治', 'indication']

        def scan_file(fpath, label):
            if not os.path.exists(fpath): return
            try:
                df = pd.read_csv(fpath, encoding='utf-8-sig').fillna("")
            except:
                df = pd.read_csv(fpath, encoding='gbk').fillna("")

            print(f"   🔍 扫描 {label}...", end="\r")
            cols = df.columns.tolist()
            for _, row in df.iterrows():
                name = row.get('名称') or row.get('name') or row.get('药名')
                if not name: continue

                # 提取主治文本
                text = ""
                for col in cols:
                    if any(k in col for k in keywords) and pd.notna(row[col]):
                        text += str(row[col]) + " "

                # 匹配疾病
                for dis in disease_list:
                    if dis in text:
                        relationships.append([name, dis, label])

        scan_file(file_pres, 'Prescription')
        scan_file(file_herb, 'Herb')

        print("   ✅ 扫描完成。                    ")
        if relationships:
            pd.DataFrame(relationships, columns=['source', 'target', 'type']).to_csv(out_file, index=False,
                                                                                     encoding='utf-8-sig')
            print(f"   💾 已生成: rel_treatment_final.csv ({len(relationships)} 条)")

    # --- 5. 业务逻辑：导入 Neo4j ---
    def import_all(self):
        print("\n🚀 [Step 2] 开始全量导入...")

        # A. 节点
        # 方剂
        data = self.load_csv("node_prescription_final.csv", {
            'name': ['名称', 'name'], 'intro': ['方解', 'explanation'],
            'usage': ['用法', 'usage'], 'clinical': ['临床应用'], 'other': ['其他信息']
        })
        if data: self.batch_run("""UNWIND $batch AS row MERGE (p:Prescription {name: row.name}) 
                                   SET p.方解=row.intro, p.用法=row.usage, p.临床应用=row.clinical, p.其他信息=row.other""",
                                data, "方剂节点")

        # 中药
        data = self.load_csv("node_herb_final.csv", {
            'name': ['名称', 'name'], 'flavor': ['性味'], 'part': ['入药部位'], 'other': ['其他信息']
        })
        if data: self.batch_run("""UNWIND $batch AS row MERGE (h:Herb {name: row.name}) 
                                   SET h.性味=row.flavor, h.入药部位=row.part, h.其他信息=row.other""", data,
                                "中药节点")

        # 疾病
        data = self.load_csv("nodes_diseases.csv", {
            'id': ['id'], 'name': ['name', '名称'], 'intro': ['introduction', '简介'], 'cause': ['cause', '病因']
        })
        if data: self.batch_run("""UNWIND $batch AS row MERGE (d:Disease {name: row.name}) 
                                   SET d.id=toString(row.id), d.简介=row.intro, d.病因=row.cause""", data, "疾病节点")

        # 症状 & 科室
        data = self.load_csv("nodes_symptoms.csv", {'name': ['name']})
        if data: self.batch_run("UNWIND $batch AS row MERGE (s:Symptom {name: row.name})", data, "症状节点")

        data = self.load_csv("nodes_departments.csv", {'name': ['name']})
        if data: self.batch_run("UNWIND $batch AS row MERGE (d:Department {name: row.name})", data, "科室节点")

        # B. 关系
        # 疾病 -> 症状 (通过 ID 匹配)
        data = self.load_csv("edges_disease_symptom.csv", {'did': ['disease_id'], 'sname': ['symptom_name', 'name']})
        if data: self.batch_run("""UNWIND $batch AS row MATCH (d:Disease) WHERE d.id = toString(row.did)
                                   MATCH (s:Symptom {name: row.sname}) MERGE (d)-[:HAS_SYMPTOM]->(s)""", data,
                                "关系: 疾病->症状")

        # 疾病 -> 科室 (通过 ID 匹配)
        data = self.load_csv("edges_disease_department.csv",
                             {'did': ['disease_id'], 'dname': ['department_name', 'name']})
        if data: self.batch_run("""UNWIND $batch AS row MATCH (d:Disease) WHERE d.id = toString(row.did)
                                   MATCH (dep:Department {name: row.dname}) MERGE (d)-[:BELONGS_TO]->(dep)""", data,
                                "关系: 疾病->科室")

        # 方剂/中药 -> 疾病 (通过 Step 1 生成的文件)
        data = self.load_csv("rel_treatment_final.csv", {'src': ['source'], 'tgt': ['target'], 'type': ['type']})
        if data:
            pres = [x for x in data if x['type'] == 'Prescription']
            herb = [x for x in data if x['type'] == 'Herb']
            self.batch_run(
                "UNWIND $batch AS row MATCH (p:Prescription {name: row.src}) MATCH (d:Disease {name: row.tgt}) MERGE (p)-[:TREATS]->(d)",
                pres, "关系: 方剂->疾病")
            self.batch_run(
                "UNWIND $batch AS row MATCH (h:Herb {name: row.src}) MATCH (d:Disease {name: row.tgt}) MERGE (h)-[:TREATS]->(d)",
                herb, "关系: 中药->疾病")


# ================= 主程序 =================
if __name__ == "__main__":
    builder = TCMGraphBuilder(NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD)
    try:
        builder.clear_database()  # 1. 清空
        builder.build_treatment_bridge()  # 2. 准备数据
        builder.import_all()  # 3. 导入

        print("\n🎉🎉🎉 完美！数据库已重置并全量导入。")
        print("快去 Neo4j 看看吧！")
    finally:
        builder.close()