import json
import csv
import glob
import os
import time
import sys
import httpx  # 必须安装: pip install httpx
from openai import OpenAI

# ================= 配置区域 =================
API_KEY = "sk-mpgfuabxekkslpreghzyehsgrnsvtwthmalwlxqlsdlndxwg"  # 🔴 请填入你的 Key
BASE_URL = "https://api.siliconflow.cn/v1"
# 推荐使用 DeepSeek-V3 进行结构化提取，性价比最高且逻辑强
MODEL_NAME = "deepseek-ai/DeepSeek-V3"

JSON_FOLDER_PATH = 'data_fangji/*.json'  # 你的JSON文件夹路径

# ================= 初始化客户端 (关键修复) =================

# 1. 解决控制台打印乱码
try:
    sys.stdout.reconfigure(encoding='utf-8')
except:
    pass

# 2. 核心修复：创建一个不读取系统代理环境的 HTTP 客户端
custom_http_client = httpx.Client(trust_env=False)

client = OpenAI(
    api_key=API_KEY,
    base_url=BASE_URL,
    http_client=custom_http_client,  # 传入自定义客户端
    max_retries=0  # 关闭库自带重试，使用我们自己的逻辑
)

# ================= 输出文件初始化 =================
# 定义文件名
files = {
    "prescriptions": "nodes_prescriptions.csv",
    "herbs": "nodes_herbs.csv",
    "literature": "nodes_literature.csv",
    "category": "nodes_category.csv",
    "cases": "nodes_cases.csv",
    "rel_composition": "edges_composition.csv",
    "rel_treats": "edges_treats.csv",
    "rel_source": "edges_source.csv",
    "rel_category": "edges_category.csv",
    "rel_has_case": "edges_has_case.csv"
}

writers = {}
file_handles = {}

# 打开所有CSV文件
for key, filename in files.items():
    f = open(filename, 'w', encoding='utf-8-sig', newline='')
    file_handles[key] = f
    writers[key] = csv.writer(f)

# 写入表头
writers['prescriptions'].writerow(
    ['id', 'name', 'effect', 'usage_text', 'explanation', 'key_points', 'modifications', 'precautions', 'other_info'])
writers['herbs'].writerow(['name'])
writers['literature'].writerow(['name'])
writers['category'].writerow(['name'])
writers['cases'].writerow(['id', 'description'])

writers['rel_composition'].writerow(['prescription', 'herb', 'dosage', 'processing'])
writers['rel_treats'].writerow(['prescription', 'target', 'usage_method'])
writers['rel_source'].writerow(['prescription', 'source'])
writers['rel_category'].writerow(['prescription', 'category'])
writers['rel_has_case'].writerow(['prescription', 'case_id'])


# ================= API 处理函数 =================

def process_file_with_api(file_id, raw_text):
    """
    调用 AI 提取全量信息 (带超时、重试、截断保护)
    """
    if not raw_text or str(raw_text).strip() == "":
        return None

    # 截断保护：防止超长文本导致API报错或死机 (保留前12000字符通常足够)
    if len(raw_text) > 12000:
        print(f" [⚠️ 文本过长({len(raw_text)})，已截断] ", end="", flush=True)
        raw_text = raw_text[:12000] + "\n...(truncated)..."

    system_prompt = """
    你是一个中医知识图谱构建专家。请处理输入的方剂JSON数据，并返回严格的 JSON 格式。

    【提取要求】：
    1. **方剂信息**：
       - name: 纯净方名（去除【来源】、出自等后缀）。
       - usage_text: 提取完整的用法用量描述。
       - 其他字段：effect(功效), explanation(方解), key_points(要点), modifications(加减), precautions(注意/禁忌)。
       - other_info: 无法归类的其他文本。

    2. **组成清洗 (至关重要)**：
       - 将“组成”文本拆解为对象列表。
       - **herb**: 必须是纯净药材名（如“桂枝”）。**严禁**包含“去皮”、“先煎”、“炒”等词。
       - **dosage**: 提取数值+单位（如“9g”、“三两”）。
       - **processing**: 提取括号内或名称后的炮制/特殊说明（如“去皮”、“先煎”、“炙”）。
       - 处理“各10g”：分配给该组所有药材。

    3. **关系提取**：
       - source: 提取《伤寒论》等书名。
       - category: 提取“解表剂”等类别。
       - treats: 提取主治的疾病或症状列表。
       - cases: 从“临床应用”中提取具体医案，每个案例作为一段独立文本。

    【返回格式】：
    {
      "info": {
        "name": "String", "source": "String", "category": "String", "effect": "String", 
        "usage_text": "String", "explanation": "String", "key_points": "String", 
        "modifications": "String", "precautions": "String", "other_info": "String"
      },
      "composition": [
        {"herb": "麻黄", "dosage": "9g", "processing": "去节"},
        {"herb": "桂枝", "dosage": "6g", "processing": "去皮"}
      ],
      "treats": ["感冒", "发热"],
      "cases": ["案例1文本...", "案例2文本..."]
    }
    """

    max_retries = 3
    retry_delay = 5

    for attempt in range(max_retries):
        try:
            print(f" [API请求 {attempt + 1}]", end="", flush=True)
            start_time = time.time()

            # 设置90秒超时，防止死等
            response = client.chat.completions.create(
                model=MODEL_NAME,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"请处理此数据：\n{raw_text}"}
                ],
                response_format={"type": "json_object"},
                temperature=0.1,
                max_tokens=4000,
                timeout=90
            )

            duration = time.time() - start_time
            print(f" [耗时{duration:.1f}s]", end="", flush=True)

            result_str = response.choices[0].message.content
            # 清洗 markdown
            result_str = result_str.replace("```json", "").replace("```", "").strip()
            return json.loads(result_str)

        except Exception as e:
            error_msg = str(e)
            print(f" [❌ 错: {type(e).__name__}]", end="", flush=True)

            if "Timeout" in error_msg:
                print("(超时)", end="", flush=True)
            elif "429" in error_msg:
                print("(限流)", end="", flush=True)
                time.sleep(15)  # 限流多等一会儿

            time.sleep(retry_delay)

    print(" [❌ 放弃]", end="", flush=True)
    return None


# ================= 主程序循环 =================

def main():
    json_files = glob.glob(JSON_FOLDER_PATH)
    print(f"🚀 开始处理 {len(json_files)} 个文件...")

    processed_count = 0

    for file_path in json_files:
        file_id = os.path.basename(file_path).split('.')[0]

        # 读取文件
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                raw_content = f.read()
        except Exception as e:
            print(f"读文件失败 {file_path}: {e}")
            continue

        print(f"正在处理: {file_id} ... ", end="", flush=True)

        # 调用 API
        data = process_file_with_api(file_id, raw_content)

        if data:
            try:
                # 1. 解构数据
                info = data.get('info', {})
                comp = data.get('composition', [])
                treats = data.get('treats', [])
                cases = data.get('cases', [])

                p_name = info.get('name', '未知方剂')
                if not p_name: p_name = "未知方剂"

                # 2. 写入方剂节点
                writers['prescriptions'].writerow([
                    file_id, p_name,
                    info.get('effect', ''), info.get('usage_text', ''),
                    info.get('explanation', ''), info.get('key_points', ''),
                    info.get('modifications', ''), info.get('precautions', ''),
                    info.get('other_info', '')
                ])

                # 3. 写入组成
                for item in comp:
                    herb = item.get('herb')
                    if herb:
                        writers['herbs'].writerow([herb])
                        writers['rel_composition'].writerow([
                            p_name, herb, item.get('dosage', ''), item.get('processing', '')
                        ])

                # 4. 写入主治
                usage_prop = info.get('usage_text', '')
                for disease in treats:
                    writers['rel_treats'].writerow([p_name, disease, usage_prop])

                # 5. 写入来源
                src = info.get('source')
                if src:
                    writers['literature'].writerow([src])
                    writers['rel_source'].writerow([p_name, src])

                # 6. 写入类别
                cat = info.get('category')
                if cat:
                    writers['category'].writerow([cat])
                    writers['rel_category'].writerow([p_name, cat])

                # 7. 写入医案
                for i, case_txt in enumerate(cases):
                    case_uid = f"{file_id}_case_{i + 1}"
                    writers['cases'].writerow([case_uid, case_txt])
                    writers['rel_has_case'].writerow([p_name, case_uid])

                print(" ✅ 成功")
                processed_count += 1

            except Exception as parse_e:
                print(f" ❌ 解析写入失败: {parse_e}")
        else:
            print(" ❌ 空数据")

        # === 冷却机制 ===
        # 处理完一个文件，暂停 2 秒，防止并发过高
        # 如果遇到429报错，可以把这里改大一点
        time.sleep(2)

        # 关闭文件
    for f in file_handles.values():
        f.close()

    print("\n🎉 全部处理完成！")


if __name__ == "__main__":
    main()