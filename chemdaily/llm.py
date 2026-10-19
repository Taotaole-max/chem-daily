"""大模型调用：OpenAI 兼容接口，默认 DeepSeek。

环境变量（本地放 .env，GitHub 上放仓库 Secrets）：
  LLM_API_KEY   必填
  LLM_BASE_URL  默认 https://api.deepseek.com
  LLM_MODEL     默认 deepseek-chat
"""
import json
import os
import time

from openai import OpenAI

_client = None


def _get_client():
    global _client
    if _client is None:
        key = os.environ.get("LLM_API_KEY") or os.environ.get("DEEPSEEK_API_KEY")
        if not key:
            raise RuntimeError("没有设置 LLM_API_KEY")
        _client = OpenAI(api_key=key, base_url=os.environ.get("LLM_BASE_URL") or "https://api.deepseek.com")
    return _client


def ask_json(system, user, max_tokens=4000, temperature=0.7):
    """让模型返回 JSON 对象；解析失败重试两次。"""
    model = os.environ.get("LLM_MODEL") or "deepseek-chat"
    last = None
    for attempt in range(3):
        try:
            resp = _get_client().chat.completions.create(
                model=model,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                response_format={"type": "json_object"},
                max_tokens=max_tokens,
                temperature=temperature,
            )
            return json.loads(resp.choices[0].message.content)
        except Exception as ex:  # noqa: BLE001
            last = ex
            time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"大模型调用失败：{last}")
