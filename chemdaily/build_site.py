"""生成网页：读 data/issues/*.json，渲染成 site/ 下的静态页面。"""
import hashlib
import json
import shutil
from datetime import date
from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader, select_autoescape

from .fetch_sources import load_sources
from .pick_and_write import FIELDS

ROOT = Path(__file__).resolve().parent.parent
WEEKDAYS = "一二三四五六日"


def load_issues():
    issues = []
    for p in sorted((ROOT / "data" / "issues").glob("*.json")):
        iss = json.loads(p.read_text(encoding="utf-8"))
        d = date.fromisoformat(iss["date"])
        iss["date_cn"] = f"{d.year}年{d.month}月{d.day}日"
        iss["weekday"] = "星期" + WEEKDAYS[d.weekday()]
        issues.append(iss)
    for n, iss in enumerate(issues, 1):
        iss["number"] = n
    return issues[::-1]  # 新的在前


def build(out_dir=ROOT / "site"):
    out = Path(out_dir)
    if out.exists():
        shutil.rmtree(out)
    (out / "issues").mkdir(parents=True)

    site = yaml.safe_load((ROOT / "config" / "site.yaml").read_text(encoding="utf-8"))
    sources = load_sources(ROOT / "config" / "sources.yaml")
    groups = {}
    for s in sources:
        groups.setdefault(s["group"], []).append(s["name"])

    css = (ROOT / "static" / "style.css").read_bytes()
    (out / "style.css").write_bytes(css)
    (out / ".nojekyll").write_text("")

    env = Environment(loader=FileSystemLoader(ROOT / "templates"), autoescape=select_autoescape())
    common = {"site": site, "css_version": hashlib.md5(css).hexdigest()[:8],
              "n_sources": len(sources), "field_index": {f: i for i, f in enumerate(FIELDS)}}

    def render(name, path, **ctx):
        (out / path).write_text(env.get_template(name).render(**common, **ctx), encoding="utf-8")

    issues = load_issues()
    for k, iss in enumerate(issues):
        nav = {"prev": issues[k + 1]["date"] if k + 1 < len(issues) else None,
               "next": issues[k - 1]["date"] if k > 0 else None}
        render("issue.html.j2", f"issues/{iss['date']}.html", issue=iss, page="", root="../", **nav)
        if k == 0:
            render("issue.html.j2", "index.html", issue=iss, page="today", root="", **nav)

    if not issues:
        render("soon.html.j2", "index.html", page="today", root="", heading=site["name"],
               text="第一期正在路上，每天北京时间早上 8 点出刊。")

    render("archive.html.j2", "archive.html", issues=issues, page="archive", root="")
    render("about.html.j2", "about.html", source_groups=list(groups.items()), page="about", root="")
    hot_files = sorted((ROOT / "data" / "hot").glob("*.json"))
    if hot_files:
        hot = json.loads(hot_files[-1].read_text(encoding="utf-8"))
        render("hot.html.j2", "hot.html", hot=hot, page="hot", root="")
    else:
        render("soon.html.j2", "hot.html", page="hot", root="", heading="热榜",
               text="近 1 个月 / 3 个月 / 1 年里被引用最多的化学论文，每个时间段 10 篇，一句话讲清楚。")

    trends = load_trends()
    if trends:
        (out / "trends").mkdir()
        for k, tr in enumerate(trends):
            render("trends.html.j2", f"trends/{tr['month']}.html", tr=tr, past=trends, page="trends", root="../")
            if k == 0:
                render("trends.html.j2", "trends.html", tr=tr, past=trends, page="trends", root="")
    else:
        render("soon.html.j2", "trends.html", page="trends", root="", heading="趋势",
               text="每月一期：化学圈这个月在卷什么，哪些方向升温最快，配发文量曲线。")
    return out


def load_trends():
    trends = []
    for p in sorted((ROOT / "data" / "trends").glob("*.json"), reverse=True):
        tr = json.loads(p.read_text(encoding="utf-8"))
        y, m = tr["month"].split("-")
        tr["month_cn"] = f"{y}年{int(m)}月"
        for t in tr["rising"]:
            t["chart"] = sparkline(t["series"], tr["months"])
        trends.append(tr)
    return trends


def sparkline(series, months, w=480, h=120):
    """单系列月度折线（内联 SVG）。线画 3 个月滑动平均（单月篇数少、波动大），悬停显示当月原始篇数。"""
    smooth = [sum(series[max(0, i - 2):i + 1]) / len(series[max(0, i - 2):i + 1]) for i in range(len(series))]
    pad_l, pad_r, pad_t, pad_b = 4, 8, 10, 20
    top = max(smooth) * 1.15 or 1
    step = (w - pad_l - pad_r) / max(len(series) - 1, 1)
    xs = [pad_l + i * step for i in range(len(series))]
    ys = [pad_t + (h - pad_t - pad_b) * (1 - v / top) for v in smooth]
    base_y = h - pad_b
    path = " ".join(f"{'M' if i == 0 else 'L'}{x:.1f},{y:.1f}" for i, (x, y) in enumerate(zip(xs, ys)))
    area = f"{path} L{xs[-1]:.1f},{base_y} L{xs[0]:.1f},{base_y} Z"
    hits = "".join(
        f'<rect x="{x - step / 2:.1f}" y="0" width="{step:.1f}" height="{h}" class="hit">'
        f"<title>{m}：当月 {v} 篇</title></rect>" for x, v, m in zip(xs, series, months))
    return (
        f'<svg class="spark" viewBox="0 0 {w} {h}" role="img" aria-label="月度发文量，{months[0]} 到 {months[-1]}">'
        f'<line x1="{pad_l}" y1="{base_y}" x2="{w - pad_r}" y2="{base_y}" class="axis"/>'
        f'<path d="{area}" class="area"/><path d="{path}" class="line"/>'
        f'<circle cx="{xs[-1]:.1f}" cy="{ys[-1]:.1f}" r="4" class="dot"/>'
        f'<text x="{pad_l}" y="{h - 4}" class="tick">{months[0]}</text>'
        f'<text x="{w - pad_r}" y="{h - 4}" class="tick" text-anchor="end">{months[-1]}</text>'
        f"{hits}</svg>"
    )
