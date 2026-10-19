"""每天的总入口：抓取 → 选稿写稿 → 存档 → 生成网页。

用法：
  python run_daily.py              正常出今天这期（今天已经出过就只重建网页）
  python run_daily.py --force      重新出今天这期（覆盖）
  python run_daily.py --build-only 不抓不写，只用已有数据重建网页
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
from chemdaily.pick_and_write import make_issue  # noqa: E402

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
    args = ap.parse_args()
    load_env()

    today = datetime.now(BEIJING).date().isoformat()
    issue_path = ROOT / "data" / "issues" / f"{today}.json"

    if not args.build_only and (args.force or not issue_path.exists()):
        sources = load_sources(ROOT / "config" / "sources.yaml")
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

    print("网页已生成：", build())


if __name__ == "__main__":
    main()
