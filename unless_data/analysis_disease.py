import json
import re

# 输入和输出文件路径
input_file = 'xywy_all_diseases.json'
output_file = 'extracted_diseases_data.json'


def extract_symptoms_from_intro(intro_text):
    """
    从简介文本中提取症状信息。
    逻辑：查找"症状表现："等关键词，截取到下一个小标题（如"并发疾病"、"治疗"等）之前的内容。
    """
    if not intro_text or not isinstance(intro_text, str):
        return "暂无数据"

    # 正则表达式说明：
    # 1. (?:症状表现|临床表现|症状)[：:] -> 匹配 "症状表现：" 或 "临床表现:" 等开头
    # 2. \s* -> 忽略冒号后的空白字符
    # 3. (.*?) -> 非贪婪匹配核心内容（即我们要的症状）
    # 4. (?=...) -> 前瞻断言，匹配到以下任意一个结束标识符为止
    # 结束标识符包括：更多>、并发疾病、治疗、就诊科室、常用检查、或者文本结束($)
    pattern = r"(?:症状表现|临床表现|症状)[：:]\s*(.*?)\s*(?=更多>|并发疾病|治疗|就诊科室|常用检查|$)"

    match = re.search(pattern, intro_text, re.DOTALL)

    if match:
        symptoms = match.group(1).strip()
        # 二次清洗：有时候"更多>"可能没被断言完全过滤掉，或者有其他杂质
        symptoms = symptoms.replace("更多>", "").strip()
        return symptoms

    return "暂无数据"


def main():
    extracted_data = []
    success_count = 0
    error_count = 0

    print(f"开始处理文件: {input_file} ...")

    try:
        with open(input_file, 'r', encoding='utf-8') as f:
            for line_num, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue

                try:
                    # 逐行解析 JSON
                    item = json.loads(line)

                    # 1. 提取基础字段
                    name = item.get('name', '未知名称')
                    cause = item.get('病因', '暂无数据')
                    intro = item.get('简介', '暂无数据')
                    prevention = item.get('预防', '暂无数据')

                    # 2. 从简介中“抠”出症状
                    symptoms = extract_symptoms_from_intro(intro)

                    # 3. 存入结果字典
                    record = {
                        "名称": name,
                        "症状": symptoms,  # 提取出的纯净症状
                        "病因": cause,
                        "预防": prevention,
                        "简介": intro  # 保留原始简介以备查
                    }
                    extracted_data.append(record)
                    success_count += 1

                except json.JSONDecodeError:
                    print(f"警告: 第 {line_num} 行格式错误，已跳过。")
                    error_count += 1

        # 将结果写入新文件
        with open(output_file, 'w', encoding='utf-8') as out_f:
            json.dump(extracted_data, out_f, ensure_ascii=False, indent=2)

        print("-" * 30)
        print(f"处理完成！")
        print(f"成功提取: {success_count} 条")
        if error_count > 0:
            print(f"跳过错误行: {error_count} 条")
        print(f"结果已保存至: {output_file}")

    except FileNotFoundError:
        print(f"错误: 找不到文件 {input_file}，请确认文件路径。")


if __name__ == "__main__":
    main()