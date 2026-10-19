"""每天的总入口：抓取 → 选稿写稿 → 存档 → 生成网页。

用法：
  python run_daily.py              正常出今天这期（今天已经出过就只重建网页）
  python run_daily.py --force      重新出今天这期（覆盖）
  python run_daily.py --build-only 不抓不写，只用已有数据重建网页
  python run_daily.py --hot --trends  另外强制刷新热榜 / 趋势

热榜每周一刷新，趋势每月第一次运行时出一期；这两样失败不影响当天的日报。
"""
import argparse
import gzip
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from chemdaily.build_site import build  # noqa: E402
from chemdaily.fetch_sources import fetch_all, load_sources  # noqa: E402
from chemdaily.hot_lists import build_hot  # noqa: E402
from chemdaily.pick_and_write import make_issue  # noqa: E402
from chemdaily.trends import build_trends  # noqa: E402

BEIJING = timezone(timedelta(hours=8))
SEEN = ROOT / "data" / "seen_ids.txt"
MIN_ITEMS = 6  # 少于这个数说明哪里出问题了，宁可不出刊


def load_env():
    """本地运行时读 .env（GitHub 上用 Secrets，不需要这个文件）。"""
    p = ROOT / ".env"
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"'))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--build-only", action="store_true")
    ap.add_argument("--hot", action="store_true", help="强制刷新热榜")
    ap.add_argument("--trends", action="store_true", help="强制重出本月趋势")
    args = ap.parse_args()
    load_env()

    today_date = datetime.now(BEIJING).date()
    today = today_date.isoformat()
    issue_path = ROOT / "data" / "issues" / f"{today}.json"

    sources = load_sources(ROOT / "config" / "sources.yaml")
    if not args.build_only and (args.force or not issue_path.exists()):
        items, report = fetch_all(sources)
        print(f"抓取完成：{len(items)} 条")
        for name, r in report.items():
            if not r["kept"]:
                print(f"  ⚠ {name}: {r['how']}")

        # 当天全部条目存一份精简档案，以后做热榜和趋势用
        slim = [{k: it[k] for k in ("id", "source", "kind", "title", "doi", "date", "link")} for it in items]
        raw_path = ROOT / "data" / "raw" / f"{today}.json.gz"
        with gzip.open(raw_path, "wt", encoding="utf-8") as f:
            json.dump({"date": today, "report": report, "items": slim}, f, ensure_ascii=False)

        seen = set(SEEN.read_text(encoding="utf-8").split()) if SEEN.exists() else set()
        issue = make_issue(items, seen)
        if len(issue["items"]) < MIN_ITEMS:
            sys.exit(f"只写出了 {len(issue['items'])} 条，少于 {MIN_ITEMS} 条，今天不出刊")

        issue["date"] = today
        issue["generated_at"] = datetime.now(BEIJING).isoformat(timespec="seconds")
        issue_path.write_text(json.dumps(issue, ensure_ascii=False, indent=1), encoding="utf-8")
        with SEEN.open("a", encoding="utf-8") as f:
            f.writelines(it["id"] + "\n" for it in issue["items"])
        print(f"今天这期写好了：{len(issue['items'])} 条 → {issue_path.name}")

    if not args.build_only:
        update_extras(sources, today_date, args)

    print("网页已生成：", build())


def update_extras(sources, today, args):
    """热榜（每周一）和趋势（每月一期）。出错只打印，不影响日报发布。"""
    hot_dir, trend_dir = ROOT / "data" / "hot", ROOT / "data" / "trends"
    hot_dir.mkdir(exist_ok=True)
    trend_dir.mkdir(exist_ok=True)

    hot_path = hot_dir / f"{today.isoformat()}.json"
    if args.hot or not any(hot_dir.glob("*.json")) or (today.weekday() == 0 and not hot_path.exists()):
        try:
            hot = build_hot(sources, today)
            hot_path.write_text(json.dumps(hot, ensure_ascii=False, indent=1), encoding="utf-8")
            print("热榜已更新：", [len(w["items"]) for w in hot["windows"]])
        except Exception as ex:  # noqa: BLE001
            print("⚠ 热榜更新失败：", ex)

    trend_path = trend_dir / f"{today:%Y-%m}.json"
    if args.trends or not trend_path.exists():
        try:
            tr = build_trends(sources, today)
            trend_path.write_text(json.dumps(tr, ensure_ascii=False, indent=1), encoding="utf-8")
            print("本月趋势已生成：", tr["headline"])
        except Exception as ex:  # noqa: BLE001
            print("⚠ 趋势生成失败：", ex)


if __name__ == "__main__":
    main()
