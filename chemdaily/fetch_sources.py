"""抓取：把 config/sources.yaml 里的每个源抓下来，统一成同一种字典格式。

优先走 RSS（带配图）；RSS 被拦或为空时，有 ISSN 的期刊走 Crossref 兜底（没有配图，但标题摘要都在）。
"""
import html
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import feedparser
import requests
import yaml

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")
TIMEOUT = 25
KEEP_DAYS = 21     # RSS 只保留最近三周的条目（月刊的 RSS 日期偏旧）；已经上过日报的由 seen 记录去重
CROSSREF_DAYS = 7  # Crossref 兜底只取最近一周新建的 DOI
# Crossref 公共接口不许高并发（并发一高就 429），所有 Crossref 请求排队一个个来
_CROSSREF_LOCK = threading.Lock()

# 综合期刊（Nature、Science、PNAS 等）用这些词粗筛出化学相关的，AI 选稿时还会再判断
CHEM_WORDS = re.compile(
    r"chem|catal|molec|polymer|battery|batteries|electrolyt|electrochem|synthe|reaction|"
    r"crystal|material|oxide|metal|\bions?\b|enzym|solvent|photocatal|photovolt|carbon|hydrogen|"
    r"nitrogen|oxygen|ammonia|\bco2\b|perovskite|semiconductor|nano|\bbonds?\b|spectro|"
    r"\bdft\b|graphene|\bmofs?\b|organic|inorganic|fuel|plastic|membrane|lithium|alloy",
    re.I,
)
# 非研究内容：勘误、封面、期刊信息、讣告等
NOT_RESEARCH = re.compile(
    r"^(correction|erratum|corrigendum|addendum|issue information|cover picture|cover feature|"
    r"front cover|inside (front |back )?cover|back cover|frontispiece|masthead|editorial board|"
    r"expression of concern|author correction|publisher correction)|\(\d{4}\s*[–-]\s*\d{4}\)",
    re.I,
)
DOI_RE = re.compile(r"10\.\d{4,9}/[^\s\"<>?#]+", re.I)


def load_sources(path="config/sources.yaml"):
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)["sources"]


def _clean(text):
    """去掉 HTML 标签和多余空白。"""
    text = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def _first_img(*htmls):
    for h in htmls:
        m = re.search(r"<img[^>]+src=[\"']([^\"']+)[\"']", h or "", re.I)
        if m:
            src = html.unescape(m.group(1))
            return "https:" + src if src.startswith("//") else src
    return None


def _entry_image(e):
    for key in ("media_content", "media_thumbnail"):
        for m in e.get(key, []) or []:
            if m.get("url"):
                return m["url"]
    for l in e.get("links", []) or []:
        if l.get("rel") == "enclosure" and str(l.get("type", "")).startswith("image"):
            return l.get("href")
    contents = [c.get("value", "") for c in e.get("content", []) or []]
    return _first_img(e.get("summary", ""), *contents)


def _entry_date(e):
    for key in ("published_parsed", "updated_parsed"):
        t = e.get(key)
        if t:
            return datetime(*t[:6], tzinfo=timezone.utc)
    return None


def _entry_doi(e):
    for key in ("prism_doi", "dc_identifier", "id", "link"):
        m = DOI_RE.search(str(e.get(key, "")))
        if m:
            return m.group(0).rstrip(".").lower()
    return None


def _item(src, title, link, abstract="", image=None, date=None, doi=None, authors=""):
    return {
        "id": doi or link,
        "source": src["name"],
        "kind": src["kind"],
        "tier": src["tier"],
        "title": _clean(title),
        "link": link,
        "doi": doi,
        # Nature 系的 RSS 摘要开头带一串 "Nature, Published online: ...; doi:..."，去掉
        "abstract": re.sub(r"^.{0,200}?doi:\s*10\.\S+\s*", "", _clean(abstract))[:1500],
        "image": image,
        "date": date.isoformat() if date else None,
        "authors": authors,
    }


def fetch_rss(src):
    r = requests.get(src["rss"], headers={"User-Agent": UA}, timeout=TIMEOUT)
    r.raise_for_status()
    feed = feedparser.parse(r.content)
    items = []
    for e in feed.entries:
        # Wiley 的 summary 只是期号，摘要在 content 里；取两者中较长的
        content = " ".join(c.get("value", "") for c in e.get("content", []) or [])
        abstract = max(e.get("summary", ""), content, key=lambda t: len(_clean(t)))
        authors = ", ".join(a.get("name", "") for a in e.get("authors", []) or [])[:200]
        items.append(_item(src, e.get("title", ""), e.get("link", ""), abstract,
                           _entry_image(e), _entry_date(e), _entry_doi(e), authors))
    return items


def _crossref_get(url, params):
    with _CROSSREF_LOCK:
        for attempt in range(4):
            r = requests.get(url, params=params, headers={"User-Agent": "chem-daily (GitHub Actions)"}, timeout=TIMEOUT)
            if r.status_code != 429 and r.status_code < 500:
                break
            time.sleep(2 * (attempt + 1))
        r.raise_for_status()
        return r.json()["message"]["items"]


def fetch_crossref(src):
    """按 ISSN（期刊）或 DOI 前缀（ChemRxiv）从 Crossref 取最近一周新建的条目。"""
    since = (datetime.now(timezone.utc) - timedelta(days=CROSSREF_DAYS)).date().isoformat()
    if src.get("crossref_prefix"):
        url = "https://api.crossref.org/works"
        filt = f"prefix:{src['crossref_prefix']},type:posted-content,from-created-date:{since}"
    else:
        url = f"https://api.crossref.org/journals/{src['issn']}/works"
        filt = f"from-created-date:{since},type:journal-article"
    params = {"filter": filt, "sort": "created", "order": "desc", "rows": 80,
              "select": "DOI,title,abstract,created,URL,author"}
    items = []
    for w in _crossref_get(url, params):
        created = w.get("created", {}).get("date-time")
        date = datetime.fromisoformat(created.replace("Z", "+00:00")) if created else None
        authors = ", ".join(f"{a.get('given', '')} {a.get('family', '')}".strip()
                            for a in w.get("author", [])[:6])
        doi = w["DOI"].lower()
        items.append(_item(src, " ".join(w.get("title", [""])), f"https://doi.org/{doi}",
                           w.get("abstract", ""), None, date, doi, authors))
    return items


def fetch_one(src):
    """抓一个源，返回 (源名, 条目列表, 用了哪种方式/错误信息)。"""
    errors = []
    if src.get("rss"):
        try:
            items = fetch_rss(src)
            if items:
                return src["name"], items, "rss"
            errors.append("rss 为空")
        except Exception as ex:  # noqa: BLE001
            errors.append(f"rss 失败: {ex.__class__.__name__} {getattr(getattr(ex, 'response', None), 'status_code', '')}".strip())
    if src.get("issn") or src.get("crossref_prefix"):
        try:
            return src["name"], fetch_crossref(src), "crossref"
        except Exception as ex:  # noqa: BLE001
            errors.append(f"crossref 失败: {ex.__class__.__name__}")
    return src["name"], [], "; ".join(errors)


def _keep(item, src, cutoff):
    if not item["title"] or NOT_RESEARCH.search(item["title"]):
        return False
    if item["date"] and datetime.fromisoformat(item["date"]) < cutoff:
        return False
    if src.get("broad") and not CHEM_WORDS.search(item["title"] + " " + item["abstract"]):
        return False
    return True


def fetch_all(sources):
    """并发抓取全部源。返回 (条目列表, 每个源的抓取情况)。"""
    cutoff = datetime.now(timezone.utc) - timedelta(days=KEEP_DAYS)
    by_name = {s["name"]: s for s in sources}
    items, report = [], {}
    with ThreadPoolExecutor(max_workers=12) as pool:
        for name, got, how in pool.map(fetch_one, sources):
            kept = [it for it in got if _keep(it, by_name[name], cutoff)]
            report[name] = {"how": how, "fetched": len(got), "kept": len(kept)}
            items.extend(kept)
    # 同一篇可能从两个源进来（如 Nature 新闻和论文），按 id 去重
    seen, unique = set(), []
    for it in items:
        if it["id"] and it["id"] not in seen:
            seen.add(it["id"])
            unique.append(it)
    return unique, report


if __name__ == "__main__":
    srcs = load_sources()
    got, rep = fetch_all(srcs)
    for name, r in rep.items():
        print(f"{name:32s} {r['how']:28s} 抓到 {r['fetched']:4d}  保留 {r['kept']:4d}")
    print("合计", len(got), "条；有配图", sum(1 for i in got if i["image"]), "条")
