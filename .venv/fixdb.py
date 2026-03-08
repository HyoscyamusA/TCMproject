import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), 'TCM.db')

try:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    # 为旧表添加新列，默认值为 1（即默认算作有结果）
    c.execute('ALTER TABLE query_history ADD COLUMN has_result BOOLEAN DEFAULT 1')
    conn.commit()
    print("修复成功！已经添加 has_result 列。")
except Exception as e:
    print(f"出错或列已存在: {e}")
finally:
    conn.close()