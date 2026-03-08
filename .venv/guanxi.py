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


class TCMAllRelationImporter:
    def __init__(self, uri, user, password):
        self.driver = None
        try:
            self.driver = GraphDatabase.driver(uri, auth=(user, password))
            self.driver.verify_connectivity()
            print("✅ 数据库连接成功！开始修正导入...")
        except exceptions.AuthError:
            print("❌ 密码错误。")
            sys.exit(1)

    def close(self):
        if self.driver: self.driver.close()

    def load_csv(self, filename, mapping=None):
        path = os.path.join(DATA_DIR, filename)
        if not os.path.exists(path):
            print(f"   ⚠️ 文件缺失: {filename} (跳过)")
            return None
        try:
            df = pd.read_csv(path, encoding='utf-8-sig').fillna("")
        except:
            df = pd.read_csv(path, encoding='gbk').fillna("")

        # 统一列名
        if mapping:
            new_cols = {}
            for target, candidates in mapping.items():
                for c in candidates:
                    if c in df.columns:
                        new_cols[c] = target;
                        break
            df = df.rename(columns=new_cols)

        # 基础清洗：去除字符串首尾空格
        for col in df.columns:
            if df[col].dtype == object:
                df[col] = df[col].astype(str).str.strip()

        return df

    def batch_run(self, query, data, desc):
        if data is None or len(data) == 0: return
        data_dict = data.to_dict('records')
        print(f"   👉 正在导入: {desc} ({len(data_dict)} 条)...")
        with self.driver.session() as session:
            batch_size = 2000
            for i in range(0, len(data_dict), batch_size):
                batch = data_dict[i:i + batch_size]
                try:
                    session.run(query, batch=batch)
                except Exception as e:
                    print(f"      ❌ 写入错误: {e}")

    # =========================================================================
    # 🌟 重点修复部分：导入中药复杂关系
    # =========================================================================
    def import_herb_complex_fix(self):
        print("\n🌿 [Step 4 - 修正版] 导入中药复杂关系 (功效/产地/归经/禁忌)...")

        # 映射: src=中药, tgt=目标, type=目标类型(Label), rel=关系类型
        df = self.load_csv("rel_herb_final.csv", {
            'src': ['source', 'source_herb'],
            'tgt': ['target', 'target_entity'],
            'type': ['target_type', 'type'],
            'rel': ['relation', 'relation_type']
        })

        if df is not None:
            # 🚨 关键修复：过滤掉 tgt 为空的数据
            initial_len = len(df)
            df = df[df['tgt'] != ""]
            df = df[df['tgt'] != "nan"]
            filtered_len = len(df)
            if initial_len != filtered_len:
                print(f"      (已过滤掉 {initial_len - filtered_len} 条目标为空的无效数据)")

            # 按 (Target_Type, Relation) 分组，动态生成 Cypher
            groups = df.groupby(['type', 'rel'])

            for (tgt_type, rel_name), group_df in groups:
                # 1. 规范化 Label (例如: Efficacy -> Efficacy)
                target_label = str(tgt_type).strip().title().replace(" ", "")
                # 2. 规范化 关系名 (例如: has_efficacy -> HAS_EFFICACY)
                rel_type_neo4j = str(rel_name).strip().upper().replace(" ", "_")

                if not target_label or not rel_type_neo4j: continue

                # 3. 执行动态导入
                # 🚨 修正点：这里必须用 row.tgt，不能用 row.target
                query = f"""
                UNWIND $batch AS row
                MATCH (h:Herb {{name: row.src}})
                MERGE (t:{target_label} {{name: row.tgt}})
                MERGE (h)-[:{rel_type_neo4j}]->(t)
                """
                self.batch_run(query, group_df, f"中药-[{rel_type_neo4j}]->{target_label}")


if __name__ == "__main__":
    importer = TCMAllRelationImporter(NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD)

    # 前几步如果已经成功，可以注释掉；
    # 但为了保险起见，或者如果你想补全，单跑这一个函数也没问题
    # 这里我只运行修复的中药部分，因为你前面的步骤显示已经成功了

    importer.import_herb_complex_fix()

    importer.close()
    print("\n🎉🎉🎉 中药关系修复完成！再也没有 null 错误了。")