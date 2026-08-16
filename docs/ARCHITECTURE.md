# 三核系统架构

生产入口是 `python server.py`，网页为 `templates/index.html`。系统只包含推送中心、基本面评分和技术面量化三个产品模块。

| 模块 | 输入 | 输出 |
| --- | --- | --- |
| `fundamental.py` | 行情初筛池、已披露财务指标 | 基本面四维评分与风险说明 |
| `quant_*.py` | AkShare/Tushare日线OHLCV | 因子、滚动优化、样本外回测、收盘信号 |
| `research_core.py` | 基本面快照、量化产物 | 统一网页数据与双评分交集 |
| `email_digest.py` | 统一推送载荷 | 纯文本/HTML午间邮件 |
| `server.py` | 浏览器请求、受保护任务请求 | 三页网站及最小API |

## API

- `GET /api/status`
- `GET /api/push/status`
- `GET /api/push/preview`
- `POST /api/push/run`
- `POST /api/cron/daily-email`
- `GET /api/cron/daily-email/status`
- `GET /api/cron/wake`
- `GET /api/fundamental`
- `GET /api/fundamental/status`
- `POST /api/fundamental/run`
- `GET /api/technical`
- `POST /api/technical/sync`

推送和量化Artifact同步接口需要 `Authorization: Bearer <CRON_SECRET>`。系统不存在券商、账户、持仓、自选、交易计划或订单接口。

## 数据边界

- 基本面评分只使用已披露财务指标，不使用技术面或AI补数。
- 技术评分只使用OHLCV派生因子，T日排名从T+1收益开始验证。
- 推送交集只是两个评分结果的展示，不是第三套选股模型。
- `dispatched` 表示GitHub工作流已触发，不等于收件箱已收到。
