# 📊 A股短线交易系统

**盘前选股 · 盘中监控 · 盘后复盘**

基于东方财富/新浪财经免费API的A股短线量化交易框架。

## 功能模块

| 模块 | 文件 | 功能 |
|------|------|------|
| 配置中心 | `config.py` | 所有可调参数集中管理 |
| 数据接口 | `data_feed.py` | 股票列表、K线、实时行情、技术指标 |
| 选股引擎 | `screener.py` | 多因子评分全市场筛选 |
| 策略引擎 | `strategy.py` | 买卖信号、仓位管理、止盈止损 |
| 盘中监控 | `monitor.py` | 实时行情监控、信号预警 |
| 回测引擎 | `backtest.py` | 历史K线回测、绩效评估 |
| 复盘分析 | `analysis.py` | 交易统计、HTML报告 |

## 快速开始

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 盘前选股
python main.py screen

# 3. 盘中监控
python main.py monitor      # 单次检查
python main.py watch        # 持续监控

# 4. 回测验证
python main.py backtest

# 5. 复盘分析
python main.py analyze

# 6. 全流程
python main.py full
```

## 选股逻辑

基于5因子评分系统：

1. **放量突破 (25%)** — 成交量放大+价格突破均线
2. **均线金叉 (20%)** — MA5上穿MA10
3. **MACD信号 (20%)** — DIF上穿DEA
4. **KDJ信号 (15%)** — KDJ低位金叉
5. **量价配合 (20%)** — 阳线+放量+多头排列

## 基础过滤条件

- 市值: 30亿 ~ 2000亿
- 价格: 3 ~ 200元
- 日均成交额: ≥ 0.5亿
- 换手率: 1% ~ 20%
- 自动排除ST股票

## 策略参数

可在 `config.py` 中调整：

```python
RISK = {
    "position_pct": 0.2,    # 单票仓位20%
    "stop_loss": -0.03,     # 止损 -3%
    "take_profit": 0.06,    # 止盈 +6%
    "max_positions": 5,     # 最大持仓5只
}
```

## 数据来源

- 东方财富 API (股票列表、K线、基本面)
- 新浪财经 API (实时行情)

无需任何API密钥，免费使用。

## 注意事项

⚠️ **本系统仅供学习和研究参考，不构成投资建议。**
⚠️ **股市有风险，投资需谨慎。**
⚠️ 实盘交易需自行接入券商API。
