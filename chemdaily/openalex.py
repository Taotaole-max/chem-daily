"""OpenAlex 查询的小工具：热榜和趋势共用。

范围限定在 config/sources.yaml 里的期刊（不含预印本和新闻）：
  - 专业化学期刊：全部算
  - 综合期刊（broad: true）：只算化学、材料、化工、能源这几个领域的
这样比在整个化学领域里排更干净，不会被一堆普通期刊刷屏。

注意：不带 key 的 OpenAlex 每天限 1000 次请求，别在循环里乱查。
"""
import time

import requests

API = "https://api.openalex.org/works"
# OpenAlex 的领域编号：15 化工、16 化学、21 能源、25 材料
CHEM_FIELDS = "15|16|21|25"


def journal_scopes(sources):
    """返回两段过滤条件：专业期刊、综合期刊（加领域限制）。"""
    narrow = [s["issn"] for s in sources if s.get("issn") and s["kind"] == "paper" and not s.get("broad")]
    broad = [s["issn"] for s in sources if s.get("issn") and s["kind"] == "paper" and s.get("broad")]
    return [
        f"primary_location.source.issn:{'|'.join(narrow)}",
        f"primary_location.source.issn:{'|'.join(broad)},primary_topic.field.id:{CHEM_FIELDS}",
    ]


def get(params):
    last = None
    for attempt in range(4):
        try:
            r = requests.get(API, params=params, timeout=60)
            if r.status_code == 429 or r.status_code >= 500:
                raise RuntimeError(f"HTTP {r.status_code}")
            r.raise_for_status()
            return r.json()
        except Exception as ex:  # noqa: BLE001
            last = ex
            time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"OpenAlex 请求失败：{last}")


def abstract_text(inv):
    """OpenAlex 的摘要是倒排索引，拼回正文。"""
    if not inv:
        return ""
    words = sorted((pos, word) for word, poss in inv.items() for pos in poss)
    return " ".join(w for _, w in words)
