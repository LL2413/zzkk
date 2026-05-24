---
name: china-stock-analysis
description: Analyze China A-share stocks, sectors, and broad market conditions with a three-part framework: fundamentals, market sentiment/funds, and sector rotation. Use when the user mentions A股, 沪深, 创业板, 科创板, 北交所, 上证指数, 深证成指, a 6-digit A-share code, an A-share company/sector/concept, or asks to analyze 股票, 板块, 大盘, 财报, 资金流, 估值, or 市场热度.
---

# China Stock Market Analysis

Use this skill to produce structured A-share analysis from local data and public sources. The canonical project entry is this folder plus the repo-level scripts under `../../../scripts/`; ignore stale app files unless the user explicitly asks about the old React/Capacitor app.

## Session Bootstrap

Read `context.md` first when entering this skill. It pins the canonical
branch (`claude/stock-market-analysis-skill-9p7lc`), the 33-code
watchlist with names+industry, snapshot JSON shape, refresh/validate
commands, fallback rules, and the pre-report checklist. Daily/event
context belongs in commit messages, not in `context.md`.

## Canonical Layout

- Skill guide: `.claude/skills/china-stock-analysis/SKILL.md`
- Data fetcher: `scripts/stock.py`
- Batch refresh: `scripts/fetch_all.ps1`, `scripts/fetch_all.sh`
- One-shot collection+analysis: `scripts/run_watchlist_analysis.ps1`, `scripts/run_watchlist_analysis.sh`
- Watchlist report generator: `scripts/analyze_watchlist.py`
- Scheduled refresh: `scripts/daily_refresh.ps1`
- Daily snapshots: `data/YYYYMMDD/*.json`
- Daily reports: `reports/watchlist_YYYYMMDD.md`
- Cache: `.cache/stock/*.json`
- Logs: `logs/*.log`
- Data health check: `.claude/skills/china-stock-analysis/scripts/validate_data.py`

## Non-Negotiables

- Do not provide buy/sell/hold ratings, target prices, or point forecasts.
- Always state the data date, source, and known missing fields.
- Treat every `errors` object in fetched JSON as material context, not noise.
- If `consistency_check.status != "ok"`, quote or summarize `consistency_check.notes` and use扣非口径 as the primary operating-profit lens.
- If `financials_absolute_recent` is missing, do not make strong claims about revenue/profit scale.
- If `price_recent` is missing, do not make short-term momentum claims.
- If industry is hardcoded (`industry_hardcoded`), say the industry classification was a fallback.
- When the user asks for "latest/current/today", force refresh or verify the latest local snapshot date first.

## Workflow

1. Identify the target.
   - Stock: normalize to a 6-digit code and infer exchange prefix: `6/9 -> SH`, `0/3 -> SZ`, `4/8 -> BJ`.
   - Sector/concept: clarify whether the口径 is industry, concept, or broad market.
   - Broad market: use `market` plus relevant index/sector context.

2. Fetch or select data.
   - Prefer local snapshots in `data/YYYYMMDD` when the requested date matches the latest available data.
   - Use `--force` for current-day refreshes or when the cache may be stale.

   Windows:
   ```powershell
   C:\Users\computer\.venv\Scripts\python.exe scripts\stock.py snapshot 002281 --json
   C:\Users\computer\.venv\Scripts\python.exe scripts\stock.py snapshot 002281 --force --json
   C:\Users\computer\.venv\Scripts\python.exe scripts\stock.py market --json
   powershell -ExecutionPolicy Bypass -File scripts\fetch_all.ps1 -Refresh
   ```
   (User's machine has Windows PowerShell 5.1 only — no `pwsh`/PS7.)

   macOS/Linux:
   ```bash
   python scripts/stock.py snapshot 002281 --json
   python scripts/stock.py snapshot 002281 --force --json
   python scripts/stock.py market --json
   ./scripts/fetch_all.sh
   ```

3. Run data health checks before analysis.

   ```powershell
   C:\Users\computer\.venv\Scripts\python.exe .claude\skills\china-stock-analysis\scripts\validate_data.py --data-dir data\20260429
   ```

   Treat critical failures as blockers. Warnings can still be analyzed, but the report must list them in "数据缺口".

   For the full 33-code watchlist, prefer the one-shot pipeline instead of
   hand-writing the report. It fetches when appropriate, runs all enrichers,
   validates data, and writes `reports/watchlist_YYYYMMDD.md`.

   Windows:
   ```powershell
   powershell -ExecutionPolicy Bypass -File scripts\run_watchlist_analysis.ps1
   powershell -ExecutionPolicy Bypass -File scripts\run_watchlist_analysis.ps1 -NoFetch -DateTag 20260522
   powershell -ExecutionPolicy Bypass -File scripts\run_watchlist_analysis.ps1 -NoFetch -DateTag 20260522 -RequireStrongFundFlow
   ```

   macOS/Linux:
   ```bash
   ./scripts/run_watchlist_analysis.sh
   ./scripts/run_watchlist_analysis.sh --no-fetch --date 20260522
   ./scripts/run_watchlist_analysis.sh --no-fetch --date 20260522 --require-strong-fund-flow
   ```

4. Fill the three cards.
   - Fundamentals: revenue, net profit,扣非净利润, ROE, gross/net margin, debt ratio, dividends, valuation, and cross-source consistency.
   - Sentiment/funds: price trend, turnover when available, main fund flow, margin data, northbound holdings, LHB, limit-up/limit-down market context.
   - Sector: industry classification, recent sector price, sector fund flow, broad sector snapshot, and whether the classification came from EM/Xueqiu/hardcoded fallback.

5. Compare before judging.
   - Avoid saying "cheap/expensive/hot/cold" without a benchmark: own history, industry median, HS300/CSI broad market, or clearly stated fallback.
   - Use 前复权 data for price history comparisons.
   - Do not mix 申万/中信/概念口径 without saying so.

6. Produce a concise report.
   - Start with one sentence of data-backed conclusion.
   - Include the three cards, risks/catalysts, data gaps, and compliance statement.

## Automation Health Rules

- Before scheduled refresh, require a clean tracked worktree. If `git pull --rebase` fails, stop; do not fetch, commit, or push on top of stale code.
- Write new JSON outputs as UTF-8. Legacy UTF-16 snapshots may be read, but should not be produced going forward.
- Validate generated `data/YYYYMMDD` before staging. Empty or unreadable snapshots must fail the run.
- After validation, generate `reports/watchlist_YYYYMMDD.md`; scheduled refresh should commit the report together with the data.
- Treat EastMoney `em_individual` / `em_rank_today_order_split` as the strong fund-flow口径. THS fallback is weak救场; when the user asks for a strong conclusion, require `--require-strong-fund-flow` or `-RequireStrongFundFlow`.
- Do not stage scratch files such as ad hoc audits unless the user asks.
- `daily_refresh.ps1` can silently skip enrichers (observed 2026-05-20: `_signal_score` missing from snapshots, required manual backfill). After every refresh, post-flight check: open the latest `data/YYYYMMDD/<code>_snapshot.json` and confirm `_signal_score` is present. If absent, re-run the enricher chain (`basic_info → valuation → margin_net → sector_flow → streak → divergence → alpha → score`) and tail the log for the enricher's `WARN` / `exit` lines before declaring the day complete.

## Report Template

```markdown
# {name} ({code}) A股分析
> 数据截至 {date}; 数据源: {sources}; 本分析仅为信息整理，不构成投资建议。

## 一句话结论
{data-backed conclusion with uncertainty}

## 盈利发展卡
- 最近 4-8 期: 营收 / 净利润 / 扣非 / ROE / 毛利率 / 负债率
- 一致性: {consistency_check.status + notes if any}
- 估值: PE/PB/股息率及对标口径

## 热度卡
- 价格与成交: {price_recent summary}
- 资金: 主力资金 / 北向 / 两融 / 龙虎榜
- 市场情绪: 涨跌停、赚钱效应、风险偏好

## 板块卡
- 行业/板块: {classification source}
- 近期表现: 板块 vs 大盘
- 板块资金与催化

## 风险与催化
- {verified risks and catalysts}

## 数据缺口
- {missing fields, endpoint errors, fallback sources}

## 合规声明
本分析仅为信息整理，不构成投资建议。A股市场存在政策风险、流动性风险、业绩变脸风险与退市风险。
```

## References

Read only the reference needed for the current request:

- `references/akshare_cookbook.md`: akshare endpoint notes.
- `references/fundamental_checklist.md`: deep fundamental checklist.
- `references/sentiment_indicators.md`: fund flow and sentiment interpretation.
- `references/sector_rotation.md`: sector rotation and industry口径.

## Common Traps

- Do not treat "主力资金净流入" as causal truth; it is an estimate from transaction classification.
- Do not use report update date as the financial period date.
- Do not infer institutional endorsement from mixed LHB seats.
- Do not ignore ST, delisting-warning, major restructuring, accounting-policy changes, or non-standard audit opinions.
- Do not silently skip failed endpoints; surface them as data gaps.
