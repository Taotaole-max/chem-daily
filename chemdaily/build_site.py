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
    render("soon.html.j2", "hot.html", page="hot", root="", heading="热榜",
           text="近 1 个月 / 3 个月 / 1 年里被引用和讨论最多的化学论文，每个时间段 10 篇，一句话讲清楚。")
    render("soon.html.j2", "trends.html", page="trends", root="", heading="趋势",
           text="每月一期：化学圈这个月在卷什么，哪些方向升温最快，配发文量曲线。")
    return out


if __name__ == "__main__":
    print("已生成：", build())
