# 化学日报 · Chem Daily

每天北京时间早上 8 点自动出一期的化学前沿日报：扫一遍约 50 个期刊、预印本和新闻网站当天的新内容，由 AI 挑出最值得看的 10 条，用大白话讲一遍，配图并附原文链接。

网址：https://taotaole-max.github.io/chem-daily/

## 怎么运作

```
GitHub Actions 每天 UTC 0 点（北京 8 点）运行 run_daily.py
  1. 抓取   chemdaily/fetch_sources.py   RSS 优先，被拦就走 Crossref
  2. 初筛   chemdaily/pick_and_write.py  期刊档次 + 新鲜度 + 重点方向 → 约 90 条候选
  3. 选稿   DeepSeek 挑 10 条、分领域（同领域最多 3 条）
  4. 写稿   DeepSeek 写中文标题 + 两三句大白话 + 导语 + 冷知识
  5. 出网页 chemdaily/build_site.py      → site/，发布到 GitHub Pages
  6. 存档   data/issues/日期.json（每期内容）、data/raw/日期.json.gz（当天全部条目）
  7. 热榜   每月一期：chemdaily/hot_lists.py，OpenAlex 被引数，近 1/3/12 个月各 10 篇
  8. 趋势   每月一期：chemdaily/trends.py，主题发文占比同比升温，配曲线
  9. 方向   日报和热榜按 12 个方向归档，每个方向一页（fields/）
 10. 搜索   search.html，纯前端：精选（日报 + 热榜，中文）+ 全部收录（每天抓到的全部条目，按月分片）
 11. 小红书 chemdaily/xhs.py：每天一篇、每月一篇草稿 + 3:4 配图，放在不进导航的工作台页面（网址见 config/site.yaml 的 xhs_path）
```

## 本地运行

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt
echo LLM_API_KEY=你的key > .env
.venv/Scripts/python run_daily.py             # 出今天这期
.venv/Scripts/python run_daily.py --build-only  # 只重建网页
```

## 常改的地方

- 加/删信息源：`config/sources.yaml`
- 网站名、统计代码：`config/site.yaml`
- 选稿标准和写作口吻：`chemdaily/pick_and_write.py` 里的 `PICK_SYSTEM`、`WRITE_SYSTEM`
- 样式：`static/style.css`
- 搜索的中文→英文对照词：`static/search.js` 里的 `SYN`
