# A股三核研究系统

系统保留三个产品入口，但午间推送按完整研究链运行：外盘与事件情景 → A股每日资金强度与板块效应 → 板块龙头 → 基本面 → 上午资金与盘中进场/止损/止盈条件。量化因子继续独立回测、次日验证和优化，但暂不参与候选过滤、综合排名或进场许可。系统不自动下单，价位计划也不等于交易指令。

## 启动网站

```bash
pip install -r requirements-quant.txt
python server.py
```

访问 `http://localhost:5000`。Render 使用 `render.yaml` 启动同一个 `server.py`。

网站只有三个页面：

1. **推送中心**：展示工作日12:00链路、外盘事件、资金强度、板块效应、龙头、双评分交集和上午盘条件价位。
2. **基本面评分**：展示质量35%、成长30%、估值20%、现金流15%的透明评分及扣分原因。
3. **技术面量化**：展示动量、趋势、波动率、量比、RSI、布林带位置、ATR、滚动样本外回测、次日验证和每日优化日志。

## 12:00 推送链路

`cron-job.org → Render /api/cron/daily-email → GitHub Actions → email_digest.py → 邮件服务`

推送分析窗口为前一交易日完整盘面和当天09:30–11:30上午盘，供13:00–14:00复核。外盘、美股、商品和地缘事件只形成情景输入，必须由A股板块资金、上涨扩散度和龙头表现确认。实际综合分为行业校准基本面66.67%、板块强度16.67%、上午个股资金强度16.67%，其中上午资金强度由主力净流入占比换算。技术量化权重为0，仅在独立研究通道继续回测、次日验证和优化；后台选股权重试验结果不自动写入实际选股。板块、基本面和上午盘触发继续作为交易许可条件；量化ATR只辅助止损距离，午后进场还必须由上午盘回踩区或突破条件触发。

Render 环境变量：`CRON_SECRET`、`GITHUB_ACTIONS_TOKEN`。GitHub Actions 使用邮件 Secrets `MAIL_USERNAME`、`MAIL_PASSWORD`、`MAIL_TO`，并可配置 `EMAIL_UNIVERSE_LIMIT`、`PUSH_FUNDAMENTAL_MIN`、`PUSH_TECHNICAL_MIN`、`PUSH_DISPLAY_LIMIT`。

`dispatched` 只说明GitHub工作流已触发；收件箱仍是最终送达凭证。

## 基本面评分

基本面总分按质量35%、成长30%、估值20%、现金流15%计算。财务数据来自东方财富已披露财务指标，页面逐股展示报告期、公告/更新日、数据源和算式。新闻不参与基本面评分，只用于外盘与事件情景分析。

运行入口位于 `fundamental.py`，统一快照由 `research_core.py` 写入 `output/research/fundamental_latest.json`。评分数据来自已披露财务指标，不使用AI补齐缺失值，也不混入短期技术信号和板块热点。

## 技术面量化

```bash
python quant_pipeline.py
```

默认工作日16:30由 `.github/workflows/quant-factor-research.yml` 运行。数据源为AkShare，配置 `TUSHARE_TOKEN` 后可优先使用Tushare。输出位于 `output/quant/`，包含HTML报告、摘要、信号、样本外净值、滚动折次、最新因子、选股验证历史和 `quant_optimization_log.json` 每日优化日志。

回测按T日收盘评分、T+1收益验证，考虑佣金万三、印花税千一和双边0.1%滑点。每天先核验上一期信号的下一交易日收益、命中率及相对全市场等权超额，再重新运行过去504个交易日训练/未来126个交易日验证的滚动优化。默认同时比较9组因子窗口与5套因子权重，共45个受约束技术方案；外层选股权重只使用逐日留存的真实午间快照及其次日收益优化。日志记录窗口、技术因子权重、选股权重、样本外指标及每次变更。单日结果只进入日志，不直接无限制改写因子定义，避免过拟合。

技术因子的原始列、横截面映射、窗口和展示说明统一登记在 `quant_factors.py` 的 `FACTOR_REGISTRY`。新增因子时先补原始因子计算，再登记映射；优化器会自动把注册项纳入权重读取和展示，权重候选方案仍需明确加入受约束网格。

量化因子用于候选排序和进场复核，并设有样本外总闸门：默认要求样本外年化与夏普均为正、最大回撤不低于-30%、样本外不少于126个交易日。任何一项不满足时，仍展示好板块、龙头和排序后的研究候选，同时保留回测与优化日志；但明确关闭实际进场许可，结论显示“不交易”，直到模型重新通过闸门。

网站可通过受保护的 `/api/technical/sync` 从最新GitHub Actions Artifact同步量化快照。12点邮件工作流通过Actions缓存读取前一日量化结果。

## 验证

```bash
python -m unittest discover -s tests -v
python -m py_compile server.py research_core.py fundamental.py email_digest.py quant_data.py quant_factors.py quant_backtest.py quant_optimizer.py quant_pipeline.py quant_journal.py selection_model.py
```

评分和回测仅用于研究，不构成投资建议，也不能提高未来收益的确定性。
