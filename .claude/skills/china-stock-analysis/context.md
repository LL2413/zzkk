# Session Context — china-stock-analysis

Long-lived bootstrap for any new session opened on this skill branch.
Do NOT write daily price/conclusion content here; this file should stay
evergreen. Daily commentary belongs in commit messages or chat replies.

## Canonical entry

- Repo: `LL2413/zzkk`
- Skill branch (canonical): `claude/stock-market-analysis-skill-9p7lc`
- Always start new sessions from this branch. `main` and other `claude/*`
  branches host an unrelated React/Capacitor app and do NOT contain the
  skill, scripts, or data dirs.
- Latest data: take the largest `data/YYYYMMDD/` directory. Do not guess
  from "today's date" — Chinese market holidays mean the latest snapshot
  may lag the calendar by several days.

## Watchlist (33 codes)

| Code   | 名称      | 行业（基本面快照口径） |
|--------|-----------|------------------------|
| 000021 | 深科技    | 半导体                 |
| 000988 | 华工科技  | 通信设备               |
| 002156 | 通富微电  | 半导体                 |
| 002281 | 光迅科技  | 通信设备               |
| 002475 | 立讯精密  | 消费电子               |
| 002837 | 英维克    | 电源设备               |
| 300395 | 菲利华    | 航空装备Ⅱ              |
| 300408 | 三环集团  | 元件                   |
| 300458 | 全志科技  | 半导体                 |
| 300499 | 高澜股份  | 电源设备               |
| 600487 | 亨通光电  | 通信设备               |
| 600522 | 中天科技  | 通信设备               |
| 600584 | 长电科技  | 半导体                 |
| 600845 | 宝信软件  | 软件开发               |
| 601138 | 工业富联  | 通信设备               |
| 601869 | 长飞光纤  | 通信设备               |
| 603256 | 宏和科技  | 电子元件               |
| 603773 | 沃格光电  | 光学光电子             |
| 603986 | 兆易创新  | 半导体                 |
| 688008 | 澜起科技  | 半导体                 |
| 688046 | 药康生物  | 医疗服务               |
| 688047 | 龙芯中科  | 半导体                 |
| 688123 | 聚辰股份  | 半导体                 |
| 688206 | 概伦电子  | 半导体                 |
| 688208 | 道通科技  | 计算机设备             |
| 688332 | 中科蓝讯  | 半导体                 |
| 688347 | 华虹公司  | 半导体                 |
| 688380 | 中微半导  | 半导体                 |
| 688521 | 芯原股份  | 半导体                 |
| 688550 | 瑞联新材  | 电子化学品Ⅱ            |
| 688627 | 精智达    | 通用设备               |
| 688728 | 格科微    | 半导体                 |
| 688981 | 中芯国际  | 半导体                 |

行业列由 `fundamentals.basic_info.行业` 字段读出，属于交易所通用口径，
与 SW/中信分类可能不一致；报告引用板块时务必声明口径。

## Data layout

每个交易日 `data/YYYYMMDD/` 目录下应含：

- `market.json`             — 大盘/赚钱效应/涨跌停统计
- `<code>_snapshot.json`    — 单股快照（33 份，全 watchlist）
- `sector_flow_aggregated.json` — 板块资金（5-13 后引入）
- `_manifest.txt`           — 当日产物清单

单股 snapshot 顶层键：
`symbol / as_of / fundamentals / sentiment / sector / _signal_score`

关键嵌套：
- `fundamentals.basic_info`（含 股票简称、总市值、行业、上市时间）
- `fundamentals.financial_indicators_recent`（最近 4-8 期 EPS/ROE 等）
- `fundamentals.financials_absolute_recent`（营收/净利润/扣非绝对值）
- `fundamentals.valuation_recent` + `valuation_latest`（PE/PB/股息率）
- `fundamentals.consistency_check`（status + notes，跨源对账）
- `fundamentals.errors`（端点抓取错误，必须读）
- `sentiment.price_recent` / `main_fund_flow` / `margin` / `lhb` / `northbound`
- `sector.classification`（含 source: em / xueqiu / hardcoded）
- `_signal_score`（3 维评分，由 enrich_score.py 产出）

## 刷新与校验命令

Windows（用户环境）：

```powershell
# 单股全量刷新
C:\Users\computer\.venv\Scripts\python.exe scripts\stock.py snapshot 002281 --force --json

# 大盘
C:\Users\computer\.venv\Scripts\python.exe scripts\stock.py market --json

# 批量全 watchlist 刷新
pwsh scripts\fetch_all.ps1 -Refresh

# 计划任务（每日收盘后跑）
pwsh scripts\daily_refresh.ps1

# 数据健康检查
C:\Users\computer\.venv\Scripts\python.exe `
  .claude\skills\china-stock-analysis\scripts\validate_data.py `
  --data-dir data\<YYYYMMDD>
```

macOS/Linux（容器/本机）：

```bash
python scripts/stock.py snapshot 002281 --force --json
python scripts/stock.py market --json
./scripts/fetch_all.sh
python .claude/skills/china-stock-analysis/scripts/validate_data.py \
  --data-dir data/<YYYYMMDD>
```

`daily_refresh.ps1` 自动按顺序跑：fetch_all → 7 个 enrich_*.py → validate_data。
若手动重跑某一步，注意维持顺序：basic_info → valuation → margin_net →
sector_flow → streak → divergence → alpha → score。

## 已知 fallback 与口径约定

- 行业分类有 EM / 雪球 / 硬编码三档；命中硬编码时 snapshot 中会有
  `industry_hardcoded` 标记，报告里须显式说明"行业为兜底分类"。
- 跨源对账以 `consistency_check.status` 为准；status != "ok" 时，
  以扣非净利润为主口径，并把 `consistency_check.notes` 抄进报告。
- 价格历史一律使用 **前复权**；不与不复权数据混用。
- 板块对比不要混用 SW / 中信 / 概念口径，必须声明。
- 估值"高/低"必须有 benchmark：自身历史分位、行业中位、HS300/中证全指。
- 涉及"今天/最新/实时"时，先核对 `data/` 里最大日期目录；与日历日不一致
  说明遇到周末或假日，使用最近交易日数据并标明日期。

## 报告前自检清单

1. `validate_data.py --data-dir data/<latest>` 通过；critical 失败 = 阻断。
2. 目标 snapshot 的 `errors` 已读，端点缺失计入"数据缺口"。
3. `consistency_check.status` 读过；非 ok 走扣非口径并抄录 notes。
4. 比较"贵/便宜/热/冷"必须配 benchmark；缺 benchmark 就降级为陈述事实。
5. 涉及短期动量须有 `sentiment.price_recent`，否则禁止短期判断。
6. 涉及营收/利润规模须有 `financials_absolute_recent`，否则禁止规模判断。
7. 板块判断须声明分类来源（EM / 雪球 / 硬编码）。
8. 全程不输出买/卖/持有评级、目标价、点位预测。

## 报告模板

输出仍走 SKILL.md 末段的报告模板（一句话结论 / 盈利发展卡 /
热度卡 / 板块卡 / 风险与催化 / 数据缺口 / 合规声明）。模板若有更新，
以 SKILL.md 为准。

## 进入分析模式的典型触发语

- "看 <code/名称> 今天怎么样" → 取最新 `data/` 目录里的 snapshot，
  跑三卡分析。
- "做 X 月 X 日完整分析" → 用 `data/YYYYMMDD/` 对应目录；缺则按
  Windows 命令 force 刷新后再做。
- "watchlist 全扫" → 读 33 只全部 snapshot + market.json +
  sector_flow_aggregated.json，按 `_signal_score` 排序后给 5-10 条要点。
