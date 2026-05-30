# 中国股市市场分析项目

这个仓库当前的主要用途是维护和运行 Claude skill：

`C:\Users\computer\zzkk\.claude\skills\china-stock-analysis`

它用于分析中国 A 股市场，覆盖三个维度：

- 公司盈利发展：财报、扣非净利润、ROE、利润率、估值和数据一致性
- 市场热度：资金流、北向、两融、龙虎榜、涨跌停和价格走势
- 板块行情：行业分类、板块走势、板块资金和轮动线索

## 主要入口

- Skill 说明：`.claude/skills/china-stock-analysis/SKILL.md`
- 数据抓取：`scripts/stock.py`
- 批量刷新：`scripts/fetch_all.ps1`
- 一键收集+分析：`scripts/run_watchlist_analysis.ps1` / `scripts/run_watchlist_analysis.sh`
- Watchlist 报告生成：`scripts/analyze_watchlist.py`
- 每日自动刷新：`scripts/daily_refresh.ps1`
- 刷新收尾器：`scripts/finalize_refresh.ps1`
- 异常补抓修复：`scripts/repair_refresh.ps1`
- 数据体检：`.claude/skills/china-stock-analysis/scripts/validate_data.py`
- 每日快照：`data/YYYYMMDD/*.json`
- 每日报告：`reports/watchlist_YYYYMMDD.md`

## 常用命令

单只股票三维快照：

```powershell
C:\Users\computer\.venv\Scripts\python.exe scripts\stock.py snapshot 002281 --json
```

强制刷新默认股票池：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\fetch_all.ps1 -Refresh
```

每日自动流水线：拉取最新代码、刷新全观察池、跑富集、校验 `_signal_score`、生成报告、提交并推送：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\daily_refresh.ps1
```

预览今天缺失或损坏的数据，不修改文件：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\repair_refresh.ps1 -DryRun
```

只补抓今天缺失或损坏的数据，成功后重新富集、校验、生成报告、提交并推送：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\repair_refresh.ps1
```

周末默认拒绝实时补抓；确实需要时显式加 `-Force`。

只补抓指定股票：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\repair_refresh.ps1 688981 601138
```

只基于已有数据重跑收尾，不抓取、不拉代码、不提交：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\repair_refresh.ps1 -NoFetch -NoPull -NoCommit
```

一键完成“抓取/补齐/校验/生成报告”（周末默认跳过抓取，分析最新已有交易日）：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\run_watchlist_analysis.ps1
```

只基于已有数据重新生成某天报告：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\run_watchlist_analysis.ps1 -NoFetch -DateTag 20260522
```

要求 EastMoney `超大单+大单` 强口径全覆盖；如果拿不到就拒绝生成强结论报告：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\run_watchlist_analysis.ps1 -NoFetch -DateTag 20260522 -RequireStrongFundFlow
```

Mac/Linux：

```bash
./scripts/run_watchlist_analysis.sh
./scripts/run_watchlist_analysis.sh --no-fetch --date 20260522
./scripts/run_watchlist_analysis.sh --no-fetch --date 20260522 --require-strong-fund-flow
```

检查某天数据完整性：

```powershell
C:\Users\computer\.venv\Scripts\python.exe .claude\skills\china-stock-analysis\scripts\validate_data.py --data-dir data\20260429
```

## 注意

本项目只做公开数据整理和分析辅助，不提供买入、卖出、持有评级，也不提供目标价。所有报告都应明确数据日期、数据来源、缺失字段和接口错误。
