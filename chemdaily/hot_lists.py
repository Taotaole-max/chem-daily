"""热榜：近 1 个月 / 3 个月 / 1 年里被引最多的化学研究论文，每段 10 篇，一句话讲清楚。

数据来自 OpenAlex 的被引数（每周更新一次就够了）。只算研究论文，不算综述——综述天然被引高，会把榜单占满。
近 1 个月的被引数普遍很小，同分时按期刊档次排。
"""
import re

from .fetch_sources import NOT_RESEARCH
from .llm import ask_json
from .openalex import abstract_text, get, journal_scopes

WINDOWS = [("1m", "近 1 个月", 30), ("3m", "近 3 个月", 90), ("12m", "近 1 年", 365)]
TOP_N = 10
# OpenAlex 的 article 类型里混着综述，按标题和综述类期刊再筛一遍
REVIEWISH = re.compile(r"\breview\b|recent (advances|progress|developments)|progress in|perspective|roadmap|"
                       r"tutorial|overview|outlook|state of the art|challenges and opportunities", re.I)
REVIEW_JOURNALS = {"Chemical Reviews", "Accounts of Chemical Research", "Chemical Society Reviews",
                   "Nature Reviews Chemistry"}

HOT_SYSTEM = """你在给一份中文化学日报写"热榜"。下面是某个时间段里被引用最多的论文（已按热度排好）。
请保留其中真正的化学/材料/能源/化工研究论文，去掉综述、观点、新闻、社论、讣告、会议摘要这类非原创研究内容，最多保留 10 篇，保持原来的顺序。
每篇写：
- title_zh：中文标题，不超过 20 字，好懂、可以轻松一点，但别夸大
- one_line：一句大白话，不超过 45 个汉字，说清楚它干了啥、为啥大家都在引用
事实只能来自给你的标题和摘要，别编数据。别用"家人们""绝绝子""不是……而是……"这种腔调。
只输出 JSON：{"items": [{"i": 编号, "title_zh": "...", "one_line": "..."}]}"""


def _candidates(sources, days, today):
    tier_by_issn = {s["issn"]: s["tier"] for s in sources if s.get("issn")}
    since = today.fromordinal(today.toordinal() - days).isoformat()
    works = {}
    for scope in journal_scopes(sources):
        data = get({
            "filter": f"{scope},type:article,from_publication_date:{since},to_publication_date:{today.isoformat()}",
            "sort": "cited_by_count:desc", "per-page": 40,
            "select": "id,doi,title,publication_date,cited_by_count,primary_location,abstract_inverted_index",
        })
        for w in data.get("results", []):
            title = w.get("title") or ""
            src = (w.get("primary_location") or {}).get("source") or {}
            if (len(title) < 15 or NOT_RESEARCH.search(title) or REVIEWISH.search(title)
                    or src.get("display_name") in REVIEW_JOURNALS):
                continue
            tier = max((tier_by_issn.get(i, 0) for i in src.get("issn") or []), default=0)
            works[w["id"]] = {
                "title_en": title,
                "source": src.get("display_name", ""),
                "cited": w.get("cited_by_count", 0),
                "date": w.get("publication_date"),
                "link": w.get("doi") or w["id"],
                "abstract": abstract_text(w.get("abstract_inverted_index"))[:600],
                "tier": tier,
            }
    return sorted(works.values(), key=lambda x: (x["cited"], x["tier"]), reverse=True)[:22]


def _write(cands):
    lines = [f"[{i}] 被引 {c['cited']}；{c['source']}\n标题：{c['title_en']}\n摘要：{c['abstract'][:400]}"
             for i, c in enumerate(cands)]
    out = ask_json(HOT_SYSTEM, "\n\n".join(lines), max_tokens=3000, temperature=0.6)
    items, used = [], set()
    for w in out.get("items", []):
        i = w.get("i")
        if isinstance(i, int) and 0 <= i < len(cands) and i not in used and w.get("title_zh"):
            used.add(i)
            c = dict(cands[i], title_zh=w["title_zh"].strip(), one_line=(w.get("one_line") or "").strip())
            c.pop("abstract", None)
            items.append(c)
    items.sort(key=lambda x: (x["cited"], x["tier"]), reverse=True)
    return items[:TOP_N]


def build_hot(sources, today):
    windows = []
    for key, label, days in WINDOWS:
        windows.append({"key": key, "label": label, "items": _write(_candidates(sources, days, today))})
    return {"updated": today.isoformat(), "windows": windows}
