import pandas as pd
from neo4j import GraphDatabase
import os
import sys

# ================= ⚙️ 核心配置 (请修改这里) =================
NEO4J_URI = "bolt://localhost:7687"
NEO4J_AUTH = ("neo4j", "12345678")  # ⚠️ 把 'your_password' 改成你的密码
DATA_DIR = r"E:\project\TCM_project1\.venv\clean_data"


# ================= 🛠️ 第一部分：数据处理与桥接构建 =================
class DataProcessor:
    def __init__(self):
        self.file_pres = os.path.join(DATA_DIR, "node_prescription_final.csv")
        self.file_herb = os.path.join(DATA_DIR, "node_herb_final.csv")
        self.file_dis = os.path.join(DATA_DIR, "nodes_diseases.csv")
        self.file_out = os.path.join(DATA_DIR, "rel_treatment_final.csv")

    def get_safe_text(self, row, columns):
        """智能提取文本：不管列名是'主治'还是'功能'，只要存在就读，防止报错"""
        text = ""
        keywords = ['主治', '功能', '功效', '功能主治', 'indication', 'efficacy']
        for col in columns:
            # 只要列名里包含关键词（比如 '功能与主治'），且内容不为空
            if any(k in col for k in keywords) and pd.notna(row[col]):
                text += str(row[col]) + " "
        return text

    def run(self):
        print("\n🚀 [Step 1] 开始构建 [方剂/中药 -> 疾病] 的治疗关系...")

        if not os.path.exists(self.file_dis):
            print("❌ 错误：找不到 nodes_diseases.csv，无法继续。")
            return False

        # 1. 读取疾病名
        try:
            df_dis = pd.read_csv(self.file_dis, encoding='utf-8')
        except:
            df_dis = pd.read_csv(self.file_dis, encoding='gbk')

        # 提取关键词 (长度>1)
        disease_list = [str(x).strip() for x in df_dis['name'].tolist() if len(str(x)) > 1]
        disease_list = list(set(disease_list))
        print(f"   📋 已加载 {len(disease_list)} 个疾病关键词。")

        relationships = []

        # 2. 扫描方剂
        if os.path.exists(self.file_pres):
            print("   🔍 正在扫描方剂主治...", end="\r")
            try:
                df_pres = pd.read_csv(self.file_pres, encoding='utf-8-sig').fillna("")
            except:
                df_pres = pd.read_csv(self.file_pres, encoding='gbk').fillna("")

            cols = df_pres.columns.tolist()
            for _, row in df_pres.iterrows():
                name = row.get('名称') or row.get('name')
                if not name: continue

                text = self.get_safe_text(row, cols)  # 智能读取
                for dis in disease_list:
                    if dis in text:
                        relationships.append([name, dis, 'Prescription'])
            print(f"   ✅ 方剂扫描完成。            ")

        # 3. 扫描中药
        if os.path.exists(self.file_herb):
            print("   🔍 正在扫描中药主治...", end="\r")
            try:
                df_herb = pd.read_csv(self.file_herb, encoding='utf-8-sig').fillna("")
            except:
                df_herb = pd.read_csv(self.file_herb, encoding='gbk').fillna("")

            cols = df_herb.columns.tolist()
            for _, row in df_herb.iterrows():
                name = row.get('名称') or row.get('name') or row.get('药名')
                if not name: continue

                text = self.get_safe_text(row, cols)  # 智能读取
                for dis in disease_list:
                    if dis in text:
                        relationships.append([name, dis, 'Herb'])
            print(f"   ✅ 中药扫描完成。            ")

        # 4. 保存文件
        if relationships:
            df_out = pd.DataFrame(relationships, columns=['source', 'target', 'type'])
            df_out.to_csv(self.file_out, index=False, encoding='utf-8-sig')
            print(f"   💾 成功生成文件: {self.file_out} (共 {len(relationships)} 条关系)")
            return True
        else:
            print("⚠️ 警告：未匹配到任何关系。")
            return True  # 虽然没关系，但流程不算失败


# ================= 🛠️ 第二部分：Neo4j 导入 =================
class Neo4jImporter:
    def __init__(self, uri, auth):
        self.driver = GraphDatabase.driver(uri, auth=auth)

    def close(self):
        self.driver.close()

    def load_csv(self, filename, mapping):
        """通用CSV读取，自动处理编码和列映射"""
        path = os.path.join(DATA_DIR, filename)
        if not os.path.exists(path):
            print(f"   ⚠️ 跳过缺失文件: {filename}")
            return None
        try:
            df = pd.read_csv(path, encoding='utf-8-sig').fillna("")
        except:
            df = pd.read_csv(path, encoding='gbk').fillna("")

        # 重命名列
        new_cols = {}
        for target, candidates in mapping.items():
            for c in candidates:
                if c in df.columns:
                    new_cols[c] = target;
                    break
        return df.rename(columns=new_cols).to_dict('records')

    def batch_run(self, query, data, desc="写入数据"):
        if not data: return
        print(f"   Execute: {desc} (共 {len(data)} 条)...")
        with self.driver.session() as session:
            batch_size = 2000
            for i in range(0, len(data), batch_size):
                batch = data[i:i + batch_size]
                try:
                    session.run(query, batch=batch)
                except Exception as e:
                    print(f"      ❌ Batch Error: {e}")

    def run_import(self):
        print("\n🚀 [Step 2] 开始导入 Neo4j 数据库...")

        # 1. 导入方剂 (包含你要的方解、其他信息)
        pres_data = self.load_csv("node_prescription_final.csv", {
            'name': ['名称', 'name'], 'intro': ['方解', 'explanation'],
            'usage': ['用法', 'usage'], 'clinical': ['临床应用'], 'other': ['其他信息']
        })
        if pres_data:
            q = """UNWIND $batch AS row MERGE (p:Prescription {name: row.name}) 
                   SET p.方解=row.intro, p.用法=row.usage, p.临床应用=row.clinical, p.其他信息=row.other"""
            self.batch_run(q, pres_data, "方剂节点")

        # 2. 导入中药
        herb_data = self.load_csv("node_herb_final.csv", {
            'name': ['名称', 'name'], 'flavor': ['性味'], 'part': ['入药部位'], 'other': ['其他信息']
        })
        if herb_data:
            q = """UNWIND $batch AS row MERGE (h:Herb {name: row.name}) 
                   SET h.性味=row.flavor, h.入药部位=row.part, h.其他信息=row.other"""
            self.batch_run(q, herb_data, "中药节点")

        # 3. 导入疾病 (带ID)
        dis_data = self.load_csv("nodes_diseases.csv", {
            'id': ['id'], 'name': ['name', '名称'], 'intro': ['introduction', '简介'], 'cause': ['cause', '病因']
        })
        if dis_data:
            q = """UNWIND $batch AS row MERGE (d:Disease {name: row.name}) 
                   SET d.id=toString(row.id), d.简介=row.intro, d.病因=row.cause"""
            self.batch_run(q, dis_data, "疾病节点")

        # 4. 导入症状 & 科室
        sym_data = self.load_csv("nodes_symptoms.csv", {'name': ['name']})
        if sym_data: self.batch_run("UNWIND $batch AS row MERGE (s:Symptom {name: row.name})", sym_data, "症状节点")

        dept_data = self.load_csv("nodes_departments.csv", {'name': ['name']})
        if dept_data: self.batch_run("UNWIND $batch AS row MERGE (d:Department {name: row.name})", dept_data,
                                     "科室节点")

        # 5. 导入 疾病->症状/科室 关系 (ID匹配)
        rel_sym = self.load_csv("edges_disease_symptom.csv", {'did': ['disease_id'], 'sname': ['symptom_name', 'name']})
        if rel_sym:
            q = """UNWIND $batch AS row MATCH (d:Disease) WHERE d.id = toString(row.did)
                   MATCH (s:Symptom {name: row.sname}) MERGE (d)-[:HAS_SYMPTOM]->(s)"""
            self.batch_run(q, rel_sym, "关系: 疾病->症状")

        rel_dept = self.load_csv("edges_disease_department.csv",
                                 {'did': ['disease_id'], 'dname': ['department_name', 'name']})
        if rel_dept:
            q = """UNWIND $batch AS row MATCH (d:Disease) WHERE d.id = toString(row.did)
                   MATCH (dep:Department {name: row.dname}) MERGE (d)-[:BELONGS_TO]->(dep)"""
            self.batch_run(q, rel_dept, "关系: 疾病->科室")

        # 6. 导入 治疗关系 (刚才生成的)
        treat_data = self.load_csv("rel_treatment_final.csv", {'src': ['source'], 'tgt': ['target'], 'type': ['type']})
        if treat_data:
            pres_treat = [x for x in treat_data if x['type'] == 'Prescription']
            herb_treat = [x for x in treat_data if x['type'] == 'Herb']

            q1 = "UNWIND $batch AS row MATCH (p:Prescription {name: row.src}) MATCH (d:Disease {name: row.tgt}) MERGE (p)-[:TREATS]->(d)"
            self.batch_run(q1, pres_treat, "关系: 方剂->疾病")

            q2 = "UNWIND $batch AS row MATCH (h:Herb {name: row.src}) MATCH (d:Disease {name: row.tgt}) MERGE (h)-[:TREATS]->(d)"
            self.batch_run(q2, herb_treat, "关系: 中药->疾病")


# ================= ▶️ 主程序 =================
if __name__ == "__main__":
    # 1. 先跑数据处理
    processor = DataProcessor()
    success = processor.run()

    if success:
        # 2. 如果成功，再跑导入
        importer = Neo4jImporter(NEO4J_URI, NEO4J_AUTH)
        try:
            importer.run_import()
            print("\n🎉🎉🎉 全部完成！你的知识图谱现在是完整的了。")
            print("👇 验证查询：")
            print(
                "MATCH path = (p:Prescription)-[:TREATS]->(d:Disease)-[:HAS_SYMPTOM]->(s:Symptom) RETURN path LIMIT 1")
        except Exception as e:
            print(f"\n❌ 数据库连接错误: {e}")
            print("请检查 Neo4j 是否启动，以及密码是否正确。")
        finally:
            importer.close()
    else:
        print("\n❌ 数据预处理失败，未执行导入。")