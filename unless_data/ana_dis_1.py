import json
import re

input_file = 'xywy_all_diseases.json'
output_file = 'extracted_diseases_clean.json'


def clean_text(text, is_list=False):
    """
    清洗文本工具函数：
    1. 去除 '更多>' '详情>' 等网页残留词。
    2. 去除 \n (换行) 和 \r (回车)。
    3. is_list=True 时，将换行替换为逗号（用于症状）。
    4. is_list=False 时，将换行替换为空格（用于长段落）。
    """
    if not text or not isinstance(text, str):
        return "暂无数据"

    # 1. 去除网页残留关键词
    text = text.replace("更多>", "").replace("详情>", "").replace("...", "")

    # 2. 处理换行符
    if is_list:
        # 如果是症状列表，把换行符变成逗号，看起来像：咳嗽，发热，头痛
        # 先把连续的空白字符变成一个换行，再替换为逗号
        text = re.sub(r'\s+', '，', text.strip())
        # 去除开头或结尾可能多余的逗号
        text = text.strip('，')
    else:
        # 如果是普通段落，把换行符变成空格，或者直接删掉
        # 这里使用正则将所有连续空白（包括\n, \t, 空格）替换为一个空格
        text = re.sub(r'\s+', ' ', text.strip())

    return text


def extract_symptoms_raw(intro_text):
    """
    仅负责从简介中截取症状那一段原始文本
    """
    if not intro_text:
        return ""

    # 匹配规则同之前
    pattern = r"(?:症状表现|临床表现|症状)[：:]\s*(.*?)\s*(?=更多>|并发疾病|治疗|就诊科室|常用检查|$)"
    match = re.search(pattern, intro_text, re.DOTALL)

    if match:
        return match.group(1)
    return ""


def main():
    extracted_data = []
    success_count = 0
    error_count = 0

    print(f"开始处理并清洗数据: {input_file} ...")

    try:
        with open(input_file, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue

                try:
                    item = json.loads(line)

                    # --- 1. 获取原始数据 ---
                    raw_name = item.get('name', '未知名称')
                    raw_cause = item.get('病因', '暂无数据')
                    raw_intro = item.get('简介', '暂无数据')
                    raw_prev = item.get('预防', '暂无数据')

                    # --- 2. 提取原始症状文本 ---
                    raw_symptoms = extract_symptoms_raw(raw_intro)
                    if not raw_symptoms:
                        raw_symptoms = "暂无数据"

                    # --- 3. 清洗数据 (去除 \n 等) ---
                    clean_name = clean_text(raw_name)
                    # 简介、病因、预防 -> 变成连贯段落
                    clean_cause = clean_text(raw_cause)
                    clean_intro = clean_text(raw_intro)
                    clean_prev = clean_text(raw_prev)
                    # 症状 -> 变成逗号分隔的字符串
                    clean_symptoms = clean_text(raw_symptoms, is_list=True)

                    # --- 4. 存入字典 ---
                    record = {
                        "名称": clean_name,
                        "症状": clean_symptoms,
                        "病因": clean_cause,
                        "预防": clean_prev,
                        "简介": clean_intro
                    }
                    extracted_data.append(record)
                    success_count += 1

                except json.JSONDecodeError:
                    error_count += 1
                    continue

        # 保存结果
        with open(output_file, 'w', encoding='utf-8') as out_f:
            # ensure_ascii=False 保证输出的是中文而不是 \uXXXX
            # indent=2 让 JSON 文件格式化显示，方便阅读（如果不想要换行可以去掉indent）
            json.dump(extracted_data, out_f, ensure_ascii=False, indent=2)

        print("-" * 30)
        print(f"清洗完成！")
        print(f"成功处理: {success_count} 条")
        print(f"结果已保存至: {output_file}")

    except Exception as e:
        print(f"发生错误: {e}")


if __name__ == "__main__":
    main()