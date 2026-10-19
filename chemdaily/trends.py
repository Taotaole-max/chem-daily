"""趋势：每月一期"本月化学圈在卷什么"。

做法：
  1. 用 OpenAlex 按月统计过去 24 个完整月份里各研究主题的发文量（范围同热榜：我们收录的期刊）
  2. 升温程度 = 最近 3 个月该主题占全部发文的比例 ÷ 去年同期的比例
     用占比而不是篇数，抵消整体发文量增长和 OpenAlex 收录延迟的影响；用同比避开季节性
  3. 取升温最快的 6 个主题，各查 3 篇代表论文，让大模型写一段导语和每个主题一句话
请求量：24 个月 × 2 段范围 + 6 个主题 × 2 = 60 次左右，在 OpenAlex 每天 1000 次的额度内。
"""
from collections import defaultdict
from datetime import date

from .llm import ask_json
from .openalex import get, journal_scopes

N_MONTHS = 24
N_RISING = 6
N_BIGGEST = 8
MIN_RECENT = 40  # 最近 3 个月至少这么多篇才算，太小的主题比例波动太大


def _month_starts(today, n):
    """today 之前的 n 个完整月份（不含当月），按时间顺序。"""
    y, m = today.year, today.month
    out = []
    for _ in range(n):
        m -= 1
        if m == 0:
            y, m = y - 1, 12
        out.append(date(y, m, 1))
    return out[::-1]


def _month_end(d):
    nxt = date(d.year + (d.month == 12), d.month % 12 + 1, 1)
    return date.fromordinal(nxt.toordinal() - 1)


def _monthly_counts(sources, months):
    counts = defaultdict(lambda: [0] * len(months))
    names = {}
    for k, m in enumerate(months):
        for scope in journal_scopes(sources):
            data = get({"filter": f"{scope},from_publication_date:{m.isoformat()},to_publication_date:{_month_end(m).isoformat()}",
                        "group_by": "primary_topic.id", "per-page": 200})
            for g in data.get("group_by", []):
                if not g.get("key") or g["key"] == "unknown":
                    continue
                tid = g["key"].rsplit("/", 1)[-1]
                counts[tid][k] += g["count"]
                names[tid] = g.get("key_display_name", tid)
    return counts, names


def _examples(sources, tid, since, until):
    papers = []
    for scope in journal_scopes(sources):
        data = get({"filter": f"{scope},primary_topic.id:{tid},type:article,from_publication_date:{since},to_publication_date:{until}",
                    "sort": "cited_by_count:desc", "per-page": 3,
                    "select": "title,doi,id,cited_by_count,primary_location"})
        for w in data.get("results", []):
            src = ((w.get("primary_location") or {}).get("source") or {}).get("display_name", "")
            papers.append({"title_en": w.get("title") or "", "link": w.get("doi") or w["id"],
                           "source": src, "cited": w.get("cited_by_count", 0)})
    papers.sort(key=lambda p: p["cited"], reverse=True)
    return papers[:3]


TREND_SYSTEM = """你在给一份中文化学日报写月度栏目"本月化学圈在卷什么"。口吻像懂行又爱开玩笑的学长，轻松好读。
给你的是：发文占比升温最快的几个研究主题（含同比倍数和代表论文标题），以及发文量最大的几个主题。
请写：
- headline：本期标题，不超过 20 字
- intro：导语，100–160 字，概括这几个月化学圈的热点在往哪走
- rising：每个升温主题 {"i": 编号, "name_zh": 中文主题名（不超过 12 字）, "one_line": 一句话（不超过 50 字）说说它在火什么、为什么火}
- biggest：每个大主题 {"i": 编号, "name_zh": 中文主题名}
只能根据给你的主题名、数字和论文标题来写，别编造具体数据和机构。别用"家人们""绝绝子""不是……而是……"。
只输出 JSON：{"headline": "...", "intro": "...", "rising": [...], "biggest": [...]}"""


def build_trends(sources, today):
    months = _month_starts(today, N_MONTHS)
    counts, names = _monthly_counts(sources, months)
    totals = [sum(c[k] for c in counts.values()) for k in range(len(months))]
    recent, base = slice(len(months) - 3, len(months)), slice(len(months) - 15, len(months) - 12)
    tot_r, tot_b = sum(totals[recent]), sum(totals[base])

    rising = []
    for tid, series in counts.items():
        r, b = sum(series[recent]), sum(series[base])
        if r < MIN_RECENT or not tot_r or not tot_b:
            continue
        ratio = (r / tot_r) / ((b + 3) / tot_b)  # +3 防止去年几乎为 0 的主题倍数爆炸
        rising.append({"id": tid, "name_en": names[tid], "series": series, "recent": r, "base": b, "ratio": round(ratio, 2)})
    rising.sort(key=lambda t: t["ratio"], reverse=True)
    rising = rising[:N_RISING]

    biggest = sorted(({"id": tid, "name_en": names[tid], "recent": sum(s[recent])} for tid, s in counts.items()),
                     key=lambda t: t["recent"], reverse=True)[:N_BIGGEST]

    since, until = months[recent][0].isoformat(), _month_end(months[-1]).isoformat()
    for t in rising:
        t["papers"] = _examples(sources, t["id"], since, until)

    lines = ["升温最快的主题："]
    for i, t in enumerate(rising):
        titles = "；".join(p["title_en"] for p in t["papers"])
        lines.append(f"[{i}] {t['name_en']}：最近 3 个月 {t['recent']} 篇，占比是去年同期的 {t['ratio']} 倍。代表论文：{titles}")
    lines.append("\n发文量最大的主题：")
    lines += [f"[{i}] {t['name_en']}：最近 3 个月 {t['recent']} 篇" for i, t in enumerate(biggest)]
    out = ask_json(TREND_SYSTEM, "\n".join(lines), max_tokens=2500, temperature=0.7)

    for key, group in (("rising", rising), ("biggest", biggest)):
        for w in out.get(key, []):
            i = w.get("i")
            if isinstance(i, int) and 0 <= i < len(group):
                group[i]["name_zh"] = w.get("name_zh") or group[i]["name_en"]
                if w.get("one_line"):
                    group[i]["one_line"] = w["one_line"]
    for t in rising + biggest:
        t.setdefault("name_zh", t["name_en"])

    return {
        "month": today.strftime("%Y-%m"),
        "updated": today.isoformat(),
        "window": {"recent": f"{months[-3]:%Y年%m月}–{months[-1]:%m月}", "base": f"{months[-15]:%Y年%m月}–{months[-13]:%m月}"},
        "months": [m.strftime("%Y-%m") for m in months],
        "headline": out.get("headline", ""),
        "intro": out.get("intro", ""),
        "rising": rising,
        "biggest": biggest,
    }
