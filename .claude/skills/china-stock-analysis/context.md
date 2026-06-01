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

## Watchlist (36 codes)

| Code   | 名称      | 行业（基本面快照口径） |
|--------|-----------|------------------------|
| 000021 | 深科技    | 半导体                 |
| 000988 | 华工科技  | 通信设备               |
| 001270 | 铖昌科技  | 半导体                 |
| 002156 | 通富微电  | 半导体                 |
| 002281 | 光迅科技  | 通信设备               |
| 002475 | 立讯精密  | 消费电子               |
| 002837 | 英维克    | 电源设备               |
| 300395 | 菲利华    | 航空装备Ⅱ              |
| 300408 | 三环集团  | 元件                   |
| 300458 | 全志科技  | 半导体                 |
| 300499 | 高澜股份  | 电源设备               |
| 600118 | 中国卫星  | 航天航空               |
| 600487 | 亨通光电  | 通信设备               |
| 600522 | 中天科技  | 通信设备               |
| 600584 | 长电科技  | 半导体                 |
| 600845 | 宝信软件  | 软件开发               |
| 600879 | 航天电子  | 航天航空               |
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
- `<code>_snapshot.json`    — 单股快照（36 份，全 watchlist）
- `sector_flow_aggregated.json` — 板块资金（5-13 后引入）
- `_manifest.txt`           — 当日产物清单

单股 snapshot 顶层键：
`symbol / as_of / fundamentals / sentiment / sector / _signal_score`

关键嵌套：
- `fundamentals.basic_info`（含 股票简称、总市值、行业；不再采集上市时间）
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

**约定 1：每段 PowerShell 第一条命令必须是 `cd C:\Users\computer\zzkk`**。
不要让用户在任意目录起命令，否则 `logs\` `scripts\` `data\` 这些
相对路径会 PathNotFound（5-20 排查 enrich_score 时踩过坑）。

**约定 2：用户机器只有 Windows PowerShell 5.1，没有 `pwsh`（PS7）**。
跑 `.ps1` 一律用 `powershell -ExecutionPolicy Bypass -File scripts\xxx.ps1`，
不要写 `pwsh`（5-21 踩过坑）。脚本本身兼容 5.1。

```powershell
cd C:\Users\computer\zzkk

# 单股全量刷新
C:\Users\computer\.venv\Scripts\python.exe scripts\stock.py snapshot 002281 --force --json

# 大盘
C:\Users\computer\.venv\Scripts\python.exe scripts\stock.py market --json

# 批量全 watchlist 刷新
powershell -ExecutionPolicy Bypass -File scripts\fetch_all.ps1 -Refresh

# 每日刷新（一条全包：git pull --rebase → fetch_all → 10 个 enricher
#   含 enrich_score → validate → _signal_score post-flight → report
#   → commit → git push。不要再手动 git add/push）
powershell -ExecutionPolicy Bypass -File scripts\daily_refresh.ps1

# 计划任务中断后，先预览今天缺失/损坏的 snapshot，再只补抓这些文件
powershell -ExecutionPolicy Bypass -File scripts\repair_refresh.ps1 -DryRun
powershell -ExecutionPolicy Bypass -File scripts\repair_refresh.ps1
# 周末默认拒绝实时补抓；确实需要时显式加 -Force

# 只补抓指定股票
powershell -ExecutionPolicy Bypass -File scripts\repair_refresh.ps1 688981 601138

# 只对现有数据重跑富集、校验和报告，不抓取、不拉代码、不提交
powershell -ExecutionPolicy Bypass -File scripts\repair_refresh.ps1 `
  -NoFetch -NoPull -NoCommit

# 数据健康检查
C:\Users\computer\.venv\Scripts\python.exe `
  .claude\skills\china-stock-analysis\scripts\validate_data.py `
  --data-dir data\<YYYYMMDD>

# 看最新日志（绝对路径，不依赖当前目录）
Get-ChildItem C:\Users\computer\zzkk\logs\ -ErrorAction SilentlyContinue |
  Sort-Object LastWriteTime -Descending | Select-Object -First 10 Name,Length,LastWriteTime

Get-Content C:\Users\computer\zzkk\logs\daily_refresh_<YYYYMMDD>.log |
  Select-String -Pattern "enrich|WARN|exit" | Select-Object -First 40
```

macOS/Linux（容器/本机）：

```bash
python scripts/stock.py snapshot 002281 --force --json
python scripts/stock.py market --json
./scripts/fetch_all.sh
python .claude/skills/china-stock-analysis/scripts/validate_data.py \
  --data-dir data/<YYYYMMDD>
```

`daily_refresh.ps1` 自动按顺序跑：fetch_all → `finalize_refresh.ps1` →
11 个 enrich_*.py → validate_data → `_signal_score` post-flight → report。
`repair_refresh.ps1` 与它共用同一个 finalizer，只补缺失/损坏的当日 snapshot。
若手动重跑某一步，注意维持顺序：basic_info → margin_net →
em_fund_flow → fund_flow_fallback → sector_flow → daily_valuation_fallback →
valuation → streak → divergence → alpha → score。

## 已知 fallback 与口径约定

- 行业分类有 EM / 雪球 / 硬编码三档；命中硬编码时 snapshot 中会有
  `industry_hardcoded` 标记，报告里须显式说明"行业为兜底分类"。
- 跨源对账以 `consistency_check.status` 为准；status != "ok" 时，
  以扣非净利润为主口径，并把 `consistency_check.notes` 抄进报告。
- 价格历史一律使用 **前复权**；不与不复权数据混用。
- 板块对比不要混用 SW / 中信 / 概念口径，必须声明。
- 估值"高/低"必须有 benchmark：自身历史分位、行业中位、HS300/中证全指。
- 财报/分红/经营指标不按日频强刷；按财报季或 2-3 个月节奏刷新。
  披露前提醒提前 14 天触发；日更只应强刷价格、资金、估值、两融、
  北向、大盘与板块价格等日频字段。
- 涉及"今天/最新/实时"时，先核对 `data/` 里最大日期目录；与日历日不一致
  说明遇到周末或假日，使用最近交易日数据并标明日期。

## 数据接口噪声 vs 真缺口

`validate_data.py` 的 warning 多数是**噪声**，不要在报告"数据缺口"里当真缺口列。
判断口径：

- `sector_history_sw`：**噪声**。stock.py 板块价格走 EM→申万→ETF(EM)→ETF(腾讯)
  四级 fallback；只要 snapshot 里有 `sector_price_recent`（看 `sector_price_source`，
  常见 `etf_tencent_*`），数据就是齐的。2026-05-21 起 stock.py 已在 fallback
  成功后清掉这个 stale error key——若仍看到，说明该只 4 级全失败，才是真缺。
- `sector_fund_flow`（per-stock）：**噪声**。per-stock 板块资金接口常挂，但
  `data/YYYYMMDD/sector_flow_aggregated.json` 已用 watchlist 成员主力净额聚合
  重建板块资金流，分析板块资金一律用这个聚合文件。
- `market_activity`：legu 接口不稳。2026-05-21 起 stock.py 有 fallback——
  失败时用 `stock_zh_a_spot_em` 算涨跌家数写入 `market.json` 的
  `market_breadth_fallback`（up/down/flat/median_pct）。有这个字段就用它，
  缺的只是 legu 的综合活跃度指数。
- `northbound`（个别股）：真缺但影响小，可能非沪深港通标的。
- `valuation_price_adjusted_fallback`：**可用但需披露**。表示实时估值端点跳过或
  不可用，脚本以上一交易日估值为基准，按当日收盘价比例更新 PE/PB/PS 和市值。
  可用于日频评分，但报告中必须保留 warning，不得写成实时接口返回值。
- `sector.sector_price_recent`：若 fast refresh 后仍缺失，属于可见缺口。板块资金
  聚合仍可使用，但个股相对板块 alpha 必须降级为无法判断，不得伪造板块价格。
- `fund-flow price mismatch`：**真告警但不阻断采集**。表示资金行涨跌幅与
  `price_recent` 当日涨跌幅不一致，通常是慢采集跨午夜后混入下一交易日资金，
  或 fallback 返回了不同交易日数据。受影响日期的资金金额、自动信号验收和累计
  资金必须降级或排除；仍可使用独立核对后的价格历史。
- `price_recent` schema：2026-05-21 起统一为英文键（`date/open/close/high/
  low/amount`），EM 源（中文键）已在 stock.py 里 rename。读历史旧 snapshot
  时仍可能遇到中文键（`日期/收盘`），分析脚本两套键都要认。

根因：Eastmoney push2/data 服务器 `RemoteDisconnected` 限流——fallback 设计
就是为对付它，多数情况下 fallback 已拿到数据。

## 报告前自检清单

1. `validate_data.py --data-dir data/<latest>` 通过；critical 失败 = 阻断。
2. 目标 snapshot 的 `errors` 已读，端点缺失计入"数据缺口"。
3. `consistency_check.status` 读过；非 ok 走扣非口径并抄录 notes。
4. 比较"贵/便宜/热/冷"必须配 benchmark；缺 benchmark 就降级为陈述事实。
5. 涉及短期动量须有 `sentiment.price_recent`，否则禁止短期判断。
6. 涉及营收/利润规模须有 `financials_absolute_recent`，否则禁止规模判断。
7. 板块判断须声明分类来源（EM / 雪球 / 硬编码）。
8. 全程不输出买/卖/持有评级、目标价、点位预测。
9. 每日报告都要做跨文档验证：读取前一交易日报告的
   `## 下一交易日跟踪重点`，逐条对照本日 snapshot，输出
   `## 前日报告跟踪验证`，说明前日想法是验证、部分验证、未验证还是无法判断。

## 报告模板（单股）

输出仍走 SKILL.md 末段的报告模板（一句话结论 / 盈利发展卡 /
热度卡 / 板块卡 / 风险与催化 / 数据缺口 / 合规声明）。模板若有更新，
以 SKILL.md 为准。

## Watchlist 扫描输出格式（默认）

收到 "watchlist 全扫 / 全 36 只" 类请求时，**默认按主力净流入 desc 排序**
（用户若说"按评分排"则改 `_signal_score` desc）。表头固定如下：

| 代码 | 名称 | 区间% | 主力(亿) | 净占% | streak | 背离 | 标签 | 估值 | 评分 | 新 |

字段口径：
- 区间% — 区间默认为本月至最新交易日；用户指定区间（如 "5-20"）就用指定。
- 主力(亿) — `sentiment.main_fund_flow` 主力净额，单位亿元，保留两位小数。
- 净占% — 主力净额占成交额比例。
- streak — `enrich_streak.py` 输出，形如 `+2d` / `-1d`。
- 背离 — `enrich_divergence.py` 输出的量价/资金背离强度（数值越大越强）。
- 标签 — 漂移 / 均衡 / 共振 等分类标签（来自 alpha 或 divergence 枚举）。
- 估值 — `valuation_latest.pe` 取整，前缀 `PE`；缺数据写 "—"。
- 评分 — `_signal_score` 总分 + 分档（如 `+1 中` / `+3 偏多` / `-2 偏空`）。
- 新 — 5-13 扩展进来的 15 只标 `★`，老 18 只留空。

不要把"今天"硬编码进表里；按 `data/` 里的最大日期决定。

## 资金扫描摘要（表前置块）

表前默认输出三行摘要，结构固定、不写当天名字：

```
🟢 资金流入 Top N：<code 名称 +金额>, ...
   → 板块集中度备注（哪几只属同一行业 / 是否单只占板块流入大头）
🔴 资金流出 Top N：<code 名称 -金额>, ...
   → 流出归因（AI 硬件 / 消费电子 / 存储 / 光模块 / IP 设计 任一）
⚠️ 价涨主力跑（重点看）：
   筛选条件 = 当日涨幅 > 0 AND 主力净额 < 0 AND (背离 ≥ 1.5 OR 拥挤 ≥ 10)
```

N 一般取 5~6，可按个股密度调整。"价涨主力跑" 一节列每只一行：
`<名称> <涨幅%> / 主力 <金额> 亿 / 拥挤 <数> — 一句话归因`。

## Watchlist 中标记 ★ 的 15 只（5-13 扩展引入）

`000021 002156 002837 300499 600584 600845 601138 603256 603773
688047 688206 688347 688521 688627 688981`

## Watchlist 新增航天链 3 只（5-28 扩展引入）

`001270 600118 600879`

## 进入分析模式的典型触发语

- "看 <code/名称> 今天怎么样" → 取最新 `data/` 目录里的 snapshot，
  跑三卡分析。
- "做 X 月 X 日完整分析" → 用 `data/YYYYMMDD/` 对应目录；缺则按
  Windows 命令 force 刷新后再做。
- "watchlist 全扫" → 读 36 只全部 snapshot + market.json +
  sector_flow_aggregated.json，按 `_signal_score` 排序后给 5-10 条要点。
