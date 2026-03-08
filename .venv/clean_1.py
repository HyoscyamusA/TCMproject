import pandas as pd
import re
import os
import glob

# ================= ⚙️ 配置区域 =================

# 你的数据文件夹路径
DATA_DIR = r"E:\project\TCM_project1\.venv\clean_data"

# 目标文件
TARGET_FILES = ["*.csv"]

# 🗑️ 垃圾语黑名单 (这些词会直接被删掉)
GARBAGE_PHRASES = [
    "您好访客,请登陆发表！",
    "您好访客，请登陆发表！",
    "您好访客,请登录发表！",
    "名 称",
    "QQ号码",
    "地 址",
    "点击查看",
    "更多资料",
    "来源：",
    "作者：",
]


# ================= 🔧 清洗逻辑 =================

def deep_clean(text):
    """
    深度清洗函数：去除URL、广告、固定垃圾词
    """
    # 如果不是字符串（比如数字或空值），直接返回
    if not isinstance(text, str):
        return text

    # 1. 【网址清洗】去除 http/https 链接
    # 哪怕在JSON里："url": "http://..." -> "url": ""
    text = re.sub(r'http[s]?://\S+', '', text)

    # 2. 【固定垃圾词清洗】
    for phrase in GARBAGE_PHRASES:
        text = text.replace(phrase, '')

    # 3. 【广告正则清洗】
    # 去除 "中医交流可加微信入群：hbxt998" 或 "加QQ群"
    text = re.sub(r'(中医交流|加微信|入群|加群).*?[：:][\w\d]+', '', text)
    # 去除 "微信：123456" 或 "QQ：88888" (忽略大小写)
    text = re.sub(r'(?i)(微信|vx|qq)\s*[:：]?\s*[a-zA-Z0-9_-]{5,}', '', text)

    # 4. 【格式清洗】去除残留的冒号和首尾空格
    text = text.strip()
    if text.startswith("：") or text.startswith(":"):
        text = text[1:]

    return text.strip()


def process_files():
    # 获取文件
    all_files = []
    for pattern in TARGET_FILES:
        full_path = os.path.join(DATA_DIR, pattern)
        all_files.extend(glob.glob(full_path))

    all_files = list(set(all_files))

    if not all_files:
        print(f"❌ 在 {DATA_DIR} 没找到 CSV 文件！")
        return

    print(f"🚀 开始深度清洗 {len(all_files)} 个文件...")

    for file_path in all_files:
        file_name = os.path.basename(file_path)
        print(f"正在清洗: {file_name} ... ", end="")

        try:
            # 读取
            try:
                df = pd.read_csv(file_path, encoding='utf-8')
            except:
                df = pd.read_csv(file_path, encoding='gbk')

            # 删除明显的垃圾列
            drop_cols = ['url', 'source_url', 'QQ', 'qq', '微信', 'email']
            existing_drop = [c for c in drop_cols if c in df.columns]
            if existing_drop:
                df = df.drop(columns=existing_drop)

            # -------------------------------------------------
            # 🔧 修复点：自动兼容新旧 Pandas 版本
            # -------------------------------------------------
            try:
                # 新版 Pandas (2.1+) 使用 map
                df = df.map(deep_clean)
            except AttributeError:
                # 旧版 Pandas 使用 applymap
                df = df.applymap(deep_clean)
            # -------------------------------------------------

            # 保存
            df.to_csv(file_path, index=False, encoding='utf-8-sig')
            print("✅ 完成")

        except Exception as e:
            print(f"❌ 失败: {e}")

    print("\n✨ 所有文件已净化完毕！即使藏在【其他信息】里的网址也被删掉了。")


if __name__ == "__main__":
    process_files()