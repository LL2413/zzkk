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
- 每日自动刷新：`scripts/daily_refresh.ps1`
- 数据体检：`.claude/skills/china-stock-analysis/scripts/validate_data.py`
- 每日快照：`data/YYYYMMDD/*.json`

## 常用命令

单只股票三维快照：

```powershell
C:\Users\computer\.venv\Scripts\python.exe scripts\stock.py snapshot 002281 --json
```

强制刷新默认股票池：

```powershell
pwsh scripts\fetch_all.ps1 -Refresh
```

检查某天数据完整性：

```powershell
C:\Users\computer\.venv\Scripts\python.exe .claude\skills\china-stock-analysis\scripts\validate_data.py --data-dir data\20260429
```

## 注意

本项目只做公开数据整理和分析辅助，不提供买入、卖出、持有评级，也不提供目标价。所有报告都应明确数据日期、数据来源、缺失字段和接口错误。
