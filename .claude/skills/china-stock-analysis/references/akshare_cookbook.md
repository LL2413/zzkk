# akshare 速查手册 · A 股分析

以下接口均来自 `akshare`（`pip install akshare -U`），覆盖基本面 / 资金面 / 板块三维度。所有代码片段都可直接运行。

> 代码示例中统一使用 `ak` 作为 akshare 别名：`import akshare as ak`

## 1. 股票池与基本信息

```python
# 全部 A 股代码与名称（沪深京）
ak.stock_info_a_code_name()

# 个股基本信息（东方财富）
ak.stock_individual_info_em(symbol="600519")

# 判断是否 ST / 退市整理
ak.stock_zh_a_st_em()          # 当前 ST 股
ak.stock_zh_a_new()            # 次新股
```

## 2. 行情（前复权是默认习惯）

```python
# 日线前复权
ak.stock_zh_a_hist(symbol="600519", period="daily",
                   start_date="20200101", end_date="20260421",
                   adjust="qfq")

# 分时 / 实时
ak.stock_zh_a_spot_em()        # 沪深京 A 股实时行情（全市场快照）

# 指数
ak.stock_zh_index_daily_em(symbol="sh000300")  # 沪深 300
```

## 3. 基本面（财报 · 估值）

```python
# 财务指标（按报告期）
ak.stock_financial_analysis_indicator(symbol="600519")

# 三大报表
ak.stock_financial_report_sina(stock="sh600519", symbol="资产负债表")
ak.stock_financial_report_sina(stock="sh600519", symbol="利润表")
ak.stock_financial_report_sina(stock="sh600519", symbol="现金流量表")

# 业绩快报 / 业绩预告（财报季关键）
ak.stock_yjbb_em(date="20260331")   # 业绩报表
ak.stock_yjyg_em(date="20260331")   # 业绩预告
ak.stock_yjkb_em(date="20260331")   # 业绩快报

# 估值
ak.stock_a_lg_indicator(symbol="600519")  # PE / PB / 股息率历史
ak.stock_value_em(symbol="600519")

# 分红
ak.stock_fhps_em(date="20251231")
```

## 4. 市场热度（资金 · 情绪 · 龙虎榜）

```python
# 个股资金流（东方财富近 100 个交易日）
ak.stock_individual_fund_flow(stock="600519", market="sh")

# 板块资金流
ak.stock_sector_fund_flow_rank(indicator="今日", sector_type="行业资金流")

# 北向资金
ak.stock_hsgt_fund_flow_summary_em()              # 北向日度
ak.stock_hsgt_hold_stock_em(market="北向", indicator="今日排行")

# 两融
ak.stock_margin_detail_szse(date="20260421")
ak.stock_margin_sse(start_date="20260101", end_date="20260421")

# 涨跌停
ak.stock_zt_pool_em(date="20260421")          # 涨停池
ak.stock_zt_pool_previous_em(date="20260421") # 昨日涨停今日表现
ak.stock_zt_pool_dtgc_em(date="20260421")     # 跌停池
ak.stock_zt_pool_zbgc_em(date="20260421")     # 炸板池

# 龙虎榜
ak.stock_lhb_detail_em(start_date="20260101", end_date="20260421")
ak.stock_lhb_jgmmtj_em(start_date="20260101", end_date="20260421")  # 机构买卖

# 市场情绪温度计
ak.stock_market_activity_legu()   # 乐股市场活跃度：涨停/跌停/上涨/下跌家数
```

## 5. 板块 · 行业

```python
# 申万一级 / 二级 / 三级行业
ak.sw_index_first_info()
ak.sw_index_second_info()
ak.sw_index_third_info()
ak.sw_index_daily(symbol="801780")     # 申万行业日线

# 东财板块
ak.stock_board_industry_name_em()      # 行业板块列表
ak.stock_board_industry_hist_em(symbol="白酒", start_date="20240101",
                                end_date="20260421", period="daily", adjust="qfq")
ak.stock_board_concept_name_em()       # 概念板块
ak.stock_board_concept_cons_em(symbol="AI算力")

# 板块估值
ak.stock_industry_pe_ratio_cninfo(symbol="证监会行业分类",
                                  date="20260421")
```

## 6. 指数与股债性价比

```python
# 全 A 股债性价比、风险溢价（近似）
ak.stock_a_ttm_lyr()           # 全 A PE-TTM & LYR
ak.stock_a_all_pb()            # 全 A PB
ak.stock_buffett_index_lg()    # 巴菲特指数（证券化率）
```

## 7. 组合调用模板

下面是一次性生成"盈利 + 热度 + 板块"三张卡的最小工作流。

```python
import akshare as ak
import pandas as pd

def analyze(symbol: str) -> dict:
    # 1) 基本面
    fin = ak.stock_financial_analysis_indicator(symbol=symbol).tail(8)
    val = ak.stock_a_lg_indicator(symbol=symbol).tail(1)

    # 2) 热度
    flow = ak.stock_individual_fund_flow(stock=symbol,
                                         market="sh" if symbol.startswith("6") else "sz")
    flow_recent = flow.tail(20)

    # 3) 板块
    info = ak.stock_individual_info_em(symbol=symbol)
    industry = info.loc[info["item"] == "行业", "value"].iloc[0]
    board = ak.stock_board_industry_hist_em(symbol=industry,
                                            start_date="20250101",
                                            end_date="20260421",
                                            period="daily", adjust="qfq").tail(20)

    return {
        "fundamentals": fin,
        "valuation": val,
        "money_flow": flow_recent,
        "industry": industry,
        "industry_20d": board,
    }
```

## 8. 常见故障排除

| 症状 | 可能原因 | 处理 |
| --- | --- | --- |
| HTTP 403 / 429 | 东方财富反爬 | 加 `time.sleep(0.3)`，或切换 VPN / 代理 |
| 数据为空 | 报告期未披露 | 检查当日 vs 披露节奏：年报 4/30 前，一季报 4/30 前，中报 8/31 前，三季报 10/31 前 |
| 接口改名 | akshare 频繁迭代 | `pip install akshare -U`，查看 https://akshare.akfamily.xyz |
| 返回字段全是中文 | akshare 特色 | 统一 `df.columns = [...]` 改英文，避免 bug |
| 复权不一致 | `adjust` 默认不复权 | 回测、历史分位统一用 `adjust="qfq"` |

## 9. 需要付费数据才能做的事

以下只能用 Wind / iFinD / Choice，akshare 无法覆盖：

- 实时 Level-2 逐笔与十档
- 机构持仓明细（基金季报之外）
- 一致预期（卖方盈利预测均值 / 中位数）
- 行业比较的长周期细粒度数据
- 期货、可转债、期权的历史 tick

在免费数据下，要对这些缺口显式声明。
