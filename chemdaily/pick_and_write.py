"""选稿 + 写稿：从当天抓到的几千条里挑 8–10 条，写成一期日报。

流程：
  1. 规则初筛：期刊档次、新鲜度、重点方向关键词 → 约 140 条
  2. 补全信息：缺摘要的去 OpenAlex 补；新闻缺简介的去原网页取 og:description
  3. 二次打分 → 约 90 条候选交给大模型
  4. 大模型第一轮：挑 10 条、标领域（同领域最多 3 条）
  5. 给入选条目补配图（原网页 og:image）
  6. 大模型第二轮：写中文标题、两三句大白话、导语、冷知识
"""
import html
import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import requests

from .fetch_sources import UA, _clean
from .llm import ask_json

FIELDS = ["有机合成", "无机与配位", "催化", "电化学与能源", "材料化学", "高分子与软物质",
          "物理化学与光谱", "计算化学与AI", "分析化学", "化学生物学与药物", "环境与绿色化学", "化学工程"]
MAX_PER_FIELD = 3
ISSUE_SIZE = 10

# 重点方向（用户关心的）：同等条件下加分
FOCUS = re.compile(
    r"machine learning|deep learning|neural network|artificial intelligence|\bai\b|language model|"
    r"dft|density functional|molecular dynamics|ab initio|simulation|"
    r"catal|single[- ]atom|electrocatal|photocatal|"
    r"battery|batteries|lithium|sodium|solid[- ]state electrolyte|perovskite|solar|hydrogen|fuel cell|"
    r"polymer|biopolymer|hydrogel|plastic|recycl",
    re.I,
)
HYPE = re.compile(r"\bfirst\b|record|unprecedented|breakthrough|general strategy|universal|"
                  r"room[- ]temperature|decades?-old|long-standing", re.I)


# ---------- 1–3 打分与初筛 ----------

def _age_days(item, now):
    if not item["date"]:
        return 3.0
    return max(0.0, (now - datetime.fromisoformat(item["date"])).total_seconds() / 86400)


def _score(item, now, use_abstract):
    text = item["title"] + " " + item["abstract"]
    s = item["tier"] * 10 - 1.5 * _age_days(item, now)
    if FOCUS.search(text):
        s += 8
    if HYPE.search(text):
        s += 3
    if item["kind"] == "preprint":
        s -= 5
    if use_abstract and len(item["abstract"]) < 150:
        s -= 25  # 没有摘要就没法如实写，强烈降权
    return s


def _cap_by_source(items, scored, limit):
    """按分数从高到低取，单个源最多若干条，避免 Wiley 一家刷屏。"""
    count, out = Counter(), []
    for it in sorted(items, key=scored, reverse=True):
        cap = 12 if it["tier"] >= 8 else 6
        if count[it["source"]] < cap:
            out.append(it)
            count[it["source"]] += 1
        if len(out) >= limit:
            break
    return out


def _openalex_abstracts(items):
    """用 OpenAlex 批量补摘要（按 DOI，每批 40 个）。"""
    need = [it for it in items if it["doi"] and len(it["abstract"]) < 150]
    for k in range(0, len(need), 40):
        batch = need[k:k + 40]
        try:
            r = requests.get("https://api.openalex.org/works", timeout=30, params={
                "filter": "doi:" + "|".join(it["doi"] for it in batch),
                "per-page": 50, "select": "doi,abstract_inverted_index"})
            r.raise_for_status()
        except Exception:  # noqa: BLE001
            continue
        found = {}
        for w in r.json().get("results", []):
            inv = w.get("abstract_inverted_index")
            if inv and w.get("doi"):
                words = sorted((pos, word) for word, poss in inv.items() for pos in poss)
                found[w["doi"].lower().replace("https://doi.org/", "")] = " ".join(wd for _, wd in words)
        for it in batch:
            if it["doi"] in found:
                it["abstract"] = found[it["doi"]][:1500]


_META = re.compile(r"<meta[^>]+(?:property|name)=[\"'](og:image|og:description|description)[\"'][^>]*>", re.I)


def _page_meta(url):
    """取原网页的 og:image 和 og:description；拿不到就返回空。"""
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=15)
        if r.status_code != 200:
            return {}
        meta = {}
        for tag in _META.finditer(r.text[:300000]):
            content = re.search(r"content=[\"']([^\"']*)[\"']", tag.group(0), re.I)
            if content:
                meta.setdefault(tag.group(1).lower(), html.unescape(content.group(1)))
        return meta
    except Exception:  # noqa: BLE001
        return {}


def _enrich_from_pages(items, want_text):
    def work(it):
        meta = _page_meta(it["link"])
        if want_text and len(it["abstract"]) < 150:
            desc = meta.get("og:description") or meta.get("description") or ""
            if len(desc) > len(it["abstract"]):
                it["abstract"] = _clean(desc)[:1500]
        if not it["image"] and meta.get("og:image", "").startswith("http"):
            it["image"] = meta["og:image"]
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(work, items))


def shortlist(items, seen, now=None):
    now = now or datetime.now(timezone.utc)
    pool = [it for it in items if it["id"] not in seen]
    papers = [it for it in pool if it["kind"] != "news"]
    news = [it for it in pool if it["kind"] == "news"]

    rough_papers = _cap_by_source(papers, lambda it: _score(it, now, False), 120)
    rough_news = _cap_by_source(news, lambda it: _score(it, now, False), 25)
    _openalex_abstracts(rough_papers)
    _enrich_from_pages(rough_news, want_text=True)

    final_papers = _cap_by_source(rough_papers, lambda it: _score(it, now, True), 72)
    final_news = sorted(rough_news, key=lambda it: _score(it, now, True), reverse=True)[:18]
    return final_papers + final_news


# ---------- 4 第一轮：挑稿 ----------

PICK_SYSTEM = f"""你是一份中文化学日报的主编。读者是化学专业学生和对科学感兴趣的普通人，他们把这份日报当新闻刷着看。
从候选里挑出今天最值得看的 {ISSUE_SIZE} 条：
- 优先：真正的突破、影响面大、方法会被很多人用、或者普通人一听就觉得有意思的
- 必须是化学或与化学紧密相关（材料、能源、化工、化学生物学都算）；和化学无关的不要
- 论文为主，混 2–3 条新闻；预印本最多 1 条
- 同一件事（新闻报道的就是候选里的某篇论文）只选一条
- 每条标一个领域，必须从这个列表里选：{"、".join(FIELDS)}
- 同一领域最多 {MAX_PER_FIELD} 条，领域尽量分散
- 不要讣告、社论、勘误、纯综述目录
只输出 JSON：{{"picks": [{{"i": 候选编号, "field": "领域", "why": "一句话理由"}}]}}，按推荐顺序排列，最重要的放第一。"""


def pick(cands):
    lines = []
    for i, it in enumerate(cands):
        lines.append(f"[{i}] ({it['kind']}, {it['source']}) {it['title']}\n    {it['abstract'][:320]}")
    out = ask_json(PICK_SYSTEM, "候选：\n" + "\n".join(lines), max_tokens=2000, temperature=0.3)
    picks, per_field, used = [], Counter(), set()
    for p in out.get("picks", []):
        i, field = p.get("i"), p.get("field")
        if not isinstance(i, int) or not 0 <= i < len(cands) or i in used:
            continue
        if field not in FIELDS:
            field = "材料化学"
        if per_field[field] >= MAX_PER_FIELD:
            continue
        used.add(i)
        per_field[field] += 1
        it = dict(cands[i], field=field, why=p.get("why", ""))
        picks.append(it)
        if len(picks) >= ISSUE_SIZE:
            break
    return picks


# ---------- 6 第二轮：写稿 ----------

WRITE_SYSTEM = """你在写一份中文化学日报，口吻像一个懂行、爱开玩笑的化学研究生学长，在跟学弟学妹和对科学好奇的朋友聊今天看到的新东西。读者是刷着玩的，必须一眼看得进去。

每条要写：
- title_zh：中文标题，不超过 22 个字。可以皮、可以打比方，但不能夸大原文，不能标题党到失真
- blurb：2–3 句大白话，80–120 个汉字。讲清楚"做了啥"和"为啥有意思/有啥用"。专业术语第一次出现时用括号带上英文，比如"金属有机框架（MOF）"
- tags：2–3 个中文关键词

另外写：
- lead：今日导语，一句话，不超过 40 字，概括今天这期，可以幽默
- fun_fact：一条化学冷知识，{"title": "不超过 16 字", "text": "60–100 字"}。必须是教科书级别、你百分之百确定的事实，拿不准就换一条

硬性要求：
- 事实只能来自给你的标题和摘要，数字照抄，不要编造摘要里没有的数据、机构、人名或应用
- 摘要信息不够时就写得保守一点，宁可少说
- 别用这些腔调："家人们""绝绝子""yyds""不是……而是……""值得一提的是""让我们""总而言之"，不要排比三连，不要感叹号连用
- emoji 整期最多用 3 个，能不用就不用
只输出 JSON：{"lead": "...", "items": [{"i": 编号, "title_zh": "...", "blurb": "...", "tags": ["..."]}], "fun_fact": {"title": "...", "text": "..."}}"""


def write(picks):
    lines = []
    for i, it in enumerate(picks):
        lines.append(f"[{i}] 类型：{it['kind']}；来源：{it['source']}；领域：{it['field']}\n"
                     f"标题：{it['title']}\n摘要：{it['abstract'][:1400]}")
    out = ask_json(WRITE_SYSTEM, "今天入选的条目：\n\n" + "\n\n".join(lines), max_tokens=5000, temperature=0.8)
    written = {w.get("i"): w for w in out.get("items", []) if isinstance(w.get("i"), int)}
    entries = []
    for i, it in enumerate(picks):
        w = written.get(i)
        if not w or not w.get("title_zh") or not w.get("blurb"):
            continue
        entries.append({
            "title_zh": w["title_zh"].strip(),
            "blurb": w["blurb"].strip(),
            "tags": [t for t in w.get("tags", []) if isinstance(t, str)][:3],
            "field": it["field"],
            "title_en": it["title"],
            "source": it["source"],
            "kind": it["kind"],
            "link": it["link"],
            "doi": it["doi"],
            "image": it["image"],
            "authors": it["authors"],
            "id": it["id"],
        })
    fun = out.get("fun_fact") or {}
    return {
        "lead": (out.get("lead") or "").strip(),
        "items": entries,
        "fun_fact": {"title": fun.get("title", ""), "text": fun.get("text", "")} if fun.get("text") else None,
    }


def make_issue(items, seen):
    cands = shortlist(items, seen)
    picks = pick(cands)
    _enrich_from_pages([p for p in picks if not p["image"]], want_text=False)
    issue = write(picks)
    issue["stats"] = {"fetched": len(items), "candidates": len(cands)}
    return issue
