"""小红书草稿：从每天的日报、每月的趋势 + 热榜生成标题、正文、标签和 3:4 配图。

草稿存 data/xhs/*.json（只有文字和图里要放的内容）；图片在生成网页时用无头浏览器截出来，不进仓库。
发帖是人工的：工作台页面上一键复制文字、下载图片，自己在 App 里发。
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

from .llm import ask_json

WEEKDAYS = "一二三四五六日"
# 小红书会限流导流内容：正文里出现这些就删掉那一行
BANNED = re.compile(r"https?://|www\.|\.com|\.org|微信|公众号|vx|VX|私信|加群|网址|链接|网站|主页")
EMOJI = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F\u200D]")

COMMON_RULES = """硬性要求：
- 事实只能来自给你的内容，数字照抄，不编造数据、机构、人名或应用
- 正文里不能出现任何网址、链接、"网站""主页""私信""微信""公众号""加群"这类字眼——小红书会当成导流限流
- 别用这些腔调："家人们""绝绝子""yyds""不是……而是……""值得一提的是""让我们""总而言之""宝子们"，不要排比三连，不要感叹号连用
- 专业词第一次出现可以带英文缩写，但别堆术语"""

DAILY_SYSTEM = """你在运营一个刚起步的小红书号，每天发一篇"今日化学圈新鲜事"。口吻：懂行、爱开玩笑的化学研究生学长，偶尔自嘲科研日常，读者是化学/材料专业的学生和对科学好奇的人，刷着玩的。
下面是今天日报的条目（带编号，标了有没有配图）。请：
- picks：挑 4–5 条最有意思、最容易让人点开的，优先有配图的，按想展示的顺序给编号
- title：笔记标题，不超过 20 个字（硬上限），可以带 1 个 emoji，要让人想点开，但别失真
- cover_title：封面大字，不超过 14 个字，不要 emoji
- cover_lines：封面上 3 句小字预告，每句不超过 14 个字，不要 emoji，对应前 3 条 picks
- body：正文 300–550 字。开头 1–2 句钩子；然后按 picks 顺序每条一段，用 1️⃣ 2️⃣ 3️⃣ 这样开头，每段 1–2 句大白话讲清楚做了啥、有啥意思；结尾一句话引导评论（比如问大家在做什么方向），别求关注。每条的出处写"（Nature）"这种期刊名就行
- tags：6–10 个话题词，不带 # 号，比如 化学、科研日常、读研、材料，再加跟今天内容相关的
""" + COMMON_RULES + """
只输出 JSON：{"picks": [编号], "title": "...", "cover_title": "...", "cover_lines": ["...", "...", "..."], "body": "...", "tags": ["..."]}"""

MONTHLY_SYSTEM = """你在运营一个刚起步的小红书号，每月发一篇"这个月化学圈在卷什么"。口吻：懂行、爱开玩笑的化学研究生学长。
下面是本月的趋势数据（升温最快的方向、同比倍数）和近一个月被引最多的论文。请写：
- title：笔记标题，不超过 20 个字（硬上限），可以带 1 个 emoji
- cover_title：封面大字，不超过 14 个字，不要 emoji
- cover_lines：封面 3 句小字预告，每句不超过 14 个字，不要 emoji
- body：正文 300–550 字。先一句钩子；讲 3–4 个升温方向各一句（带上同比倍数）；再挑热榜里 2–3 篇各一句；结尾问大家自己的方向在不在榜上
- tags：6–10 个话题词，不带 # 号
""" + COMMON_RULES + """
只输出 JSON：{"title": "...", "cover_title": "...", "cover_lines": ["...", "...", "..."], "body": "...", "tags": ["..."]}"""


def _date_cn(d):
    d = date.fromisoformat(d)
    return f"{d.month}月{d.day}日 周{WEEKDAYS[d.weekday()]}"


def _clean_text(out):
    """限长、去 emoji（图里没有 emoji 字体）、删掉带导流字眼的行。"""
    body = "\n".join(l for l in (out.get("body") or "").splitlines() if not BANNED.search(l)).strip()
    tags = []
    for t in out.get("tags") or []:
        t = str(t).strip().lstrip("#").strip()
        if t and t not in tags and not BANNED.search(t):
            tags.append(t)
    return {
        "title": (out.get("title") or "").strip()[:20],
        "cover_title": EMOJI.sub("", out.get("cover_title") or "").strip()[:16],
        "cover_lines": [EMOJI.sub("", l).strip()[:16] for l in (out.get("cover_lines") or [])[:3]],
        "body": body,
        "tags": tags[:10],
    }


def make_daily_draft(issue):
    items = issue["items"]
    lines = [f"[{i}] {'有图' if it.get('image') else '无图'}；{it['field']}；{it['source']}\n"
             f"标题：{it['title_zh']}\n内容：{it['blurb']}" for i, it in enumerate(items)]
    out = ask_json(DAILY_SYSTEM, "\n\n".join(lines), max_tokens=2500, temperature=0.85)
    picks = [i for i in out.get("picks", []) if isinstance(i, int) and 0 <= i < len(items)]
    picks = list(dict.fromkeys(picks))[:5] or list(range(min(5, len(items))))
    text = _clean_text(out)
    chosen = [items[i] for i in picks]
    slides = [{"type": "cover", "date_cn": _date_cn(issue["date"]), "title": text["cover_title"] or issue["lead"][:14],
               "lines": text["cover_lines"], "thumbs": [it["image"] for it in chosen if it.get("image")][:3]}]
    for n, it in enumerate(chosen, 1):
        slides.append({"type": "item", "n": n, "total": len(chosen), "field": it["field"],
                       "title": it["title_zh"], "text": it["blurb"], "source": it["source"],
                       "kind": it.get("kind", "paper"), "title_en": it["title_en"], "image": it.get("image") or ""})
    return dict(text, id=issue["date"], kind="daily", date=issue["date"], slides=slides)


def make_monthly_draft(trends, hot):
    month = trends["month"]
    y, m = month.split("-")
    hot_1m = next((w["items"] for w in hot["windows"] if w["key"] == "1m"), []) if hot else []
    lines = [f"本月标题：{trends['headline']}", "升温最快的方向（同比倍数）："]
    lines += [f"- {t.get('name_zh') or t['name_en']} ×{t['ratio']}：{t.get('one_line', '')}" for t in trends["rising"]]
    lines += ["近一个月被引最多的论文："]
    lines += [f"- {h['title_zh']}（{h['source']}，被引 {h['cited']}）：{h.get('one_line', '')}" for h in hot_1m[:6]]
    out = ask_json(MONTHLY_SYSTEM, "\n".join(lines), max_tokens=2000, temperature=0.85)
    text = _clean_text(out)
    slides = [
        {"type": "cover", "date_cn": f"{y}年{int(m)}月 · 月度趋势", "title": text["cover_title"] or trends["headline"][:14],
         "lines": text["cover_lines"], "thumbs": []},
        {"type": "rising", "heading": "升温最快的方向", "sub": "主题发文占比，最近 3 个月对比去年同期",
         "rows": [{"name": t.get("name_zh") or t["name_en"], "ratio": t["ratio"], "text": t.get("one_line", "")}
                  for t in trends["rising"][:6]]},
    ]
    if hot_1m:
        slides.append({"type": "hot", "heading": "近一个月被引最多", "sub": "只算原创研究，不算综述",
                       "rows": [{"title": h["title_zh"], "source": h["source"], "cited": h["cited"],
                                 "text": h.get("one_line", "")} for h in hot_1m[:5]]})
    return dict(text, id=f"month-{month}", kind="monthly", date=trends["updated"], slides=slides)


# ---------- 出图 ----------

def find_browser():
    for p in (os.environ.get("CHROME_BIN"), shutil.which("google-chrome"), shutil.which("chromium"),
              shutil.which("chromium-browser"), shutil.which("msedge"),
              r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
              r"C:\Program Files\Google\Chrome\Application\chrome.exe"):
        if p and Path(p).exists():
            return p
    return None


def _shoot(browser, html_path, png_path):
    # 每个进程一个独立的用户目录，不然并发时会抢同一个 profile
    with tempfile.TemporaryDirectory() as prof:
        subprocess.run([browser, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--no-sandbox",
                        f"--user-data-dir={prof}", "--window-size=1080,1440", "--virtual-time-budget=15000",
                        "--force-device-scale-factor=1", f"--screenshot={png_path}", Path(html_path).as_uri()],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=90, check=False)


def render_drafts(drafts, env, out_dir, cache_dir, common):
    """把草稿的每一页渲染成 1080×1440 的 JPG，放进 out_dir/<草稿 id>/NN.jpg。
    同样的内容和模板不重复截图（本地有缓存；Actions 上每次都是新机器，会全部重截）。返回 {草稿 id: [文件名]}。"""
    from PIL import Image

    browser = find_browser()
    if not browser:
        print("⚠ 没找到 Chrome/Edge，小红书配图跳过")
        return {}
    tpl = env.get_template("xhs_slide.html.j2")
    cache_dir.mkdir(parents=True, exist_ok=True)
    jobs, result = [], {}
    tmp = Path(tempfile.mkdtemp())
    for d in drafts:
        names = []
        for k, s in enumerate(d["slides"], 1):
            html = tpl.render(s=s, **common)
            key = hashlib.md5(html.encode("utf-8")).hexdigest()[:16]
            cached = cache_dir / f"{key}.jpg"
            name = f"{k:02d}.jpg"
            names.append(name)
            if not cached.exists():
                src = tmp / f"{key}.html"
                src.write_text(html, encoding="utf-8")
                jobs.append((src, tmp / f"{key}.png", cached))
        result[d["id"]] = names

    def work(job):
        src, png, cached = job
        _shoot(browser, src, png)
        if png.exists():
            Image.open(png).convert("RGB").save(cached, "JPEG", quality=88, optimize=True)

    if jobs:
        with ThreadPoolExecutor(4) as ex:
            list(ex.map(work, jobs))
        print(f"小红书配图：新截 {len(jobs)} 张")

    for d in drafts:
        dst = out_dir / d["id"]
        dst.mkdir(parents=True, exist_ok=True)
        for k, s in enumerate(d["slides"], 1):
            html = tpl.render(s=s, **common)
            cached = cache_dir / f"{hashlib.md5(html.encode('utf-8')).hexdigest()[:16]}.jpg"
            if cached.exists():
                shutil.copyfile(cached, dst / f"{k:02d}.jpg")
        result[d["id"]] = [n for n in result[d["id"]] if (dst / n).exists()]
    shutil.rmtree(tmp, ignore_errors=True)
    return result


def load_drafts(root, days=10):
    """最近的草稿：日报草稿最近 days 篇，月度草稿最近 2 篇，新的在前。"""
    xdir = root / "data" / "xhs"
    daily = sorted(xdir.glob("2*.json"), reverse=True)[:days]
    monthly = sorted(xdir.glob("month-*.json"), reverse=True)[:2]
    drafts = [json.loads(p.read_text(encoding="utf-8")) for p in daily + monthly]
    drafts.sort(key=lambda d: (d["date"], d["kind"] == "monthly"), reverse=True)
    return drafts


if __name__ == "__main__":
    sys.exit("用 run_daily.py 跑")
