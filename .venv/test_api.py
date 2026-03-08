import os
import sys
import httpx  # 确保已安装: pip install httpx
from openai import OpenAI

# ================= 配置区域 =================
API_KEY = "sk-mpgfuabxekkslpreghzyehsgrnsvtwthmalwlxqlsdlndxwg"  # 🔴 填入你的 Key
BASE_URL = "https://api.siliconflow.cn/v1"

# 解决控制台打印中文报错
try:
    sys.stdout.reconfigure(encoding='utf-8')
except:
    pass

print(f"正在测试连接: {BASE_URL} ...")

try:
    # -------------------------------------------------
    # 核心修改：只保留 trust_env=False
    # 这就足以绕过 Windows 的中文环境编码 bug
    # -------------------------------------------------
    custom_http_client = httpx.Client(trust_env=False)

    client = OpenAI(
        api_key=API_KEY,
        base_url=BASE_URL,
        http_client=custom_http_client  # 传入自定义客户端
    )

    # 发送测试请求
    resp = client.models.list()

    print("\n✅ 连接成功！问题彻底解决。")
    print("服务器返回的模型列表前2个：")
    for model in resp.data[:2]:
        print(f" - {model.id}")

except Exception as e:
    print("\n❌ 依然失败，详细报错：")
    print(e)