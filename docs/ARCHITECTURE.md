# 三核系统架构

生产入口是 `python server.py`，网页为 `templates/index.html`。系统只包含推送中心、基本面评分和技术面量化三个产品模块。

| 模块 | 输入 | 输出 |
| --- | --- | --- |
| `fundamental.py` | 行情初筛池、已披露财务指标 | 基本面四维评分与风险说明 |
| `quant_*.py` | AkShare/Tushare日线OHLCV、上一期信号 | 因子、滚动优化、样本外回测、次日验证、每日优化日志、收盘信号 |
| `research_core.py` | 外盘/事件、板块资金与成分、基本面快照、量化产物、上午分时 | 统一网页/邮件全链路结果与条件价位 |
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

推送和量化Artifact同步接口需要 `Authorization: Bearer <CRON_SECRET>`。系统不存在券商、账户、持仓或订单接口；上午盘价位只是条件研究计划，不触发委托。

## 数据边界

- 基本面评分只使用已披露财务指标，不使用技术面或AI补数。
- 技术评分只使用OHLCV派生因子，T日排名从T+1收益开始验证；每日记录实绩后重新运行受约束滚动优化。
- 样本外年化、夏普、最大回撤或样本天数未过总闸门时，技术排名不进入选股和进场链路。
- 推送先按基本面与量化形成交集，再依次检查板块资金/效应和上午盘触发；不把排名直接解释为买点。
- 外盘和地缘事件是情景输入，不直接替代A股资金确认。
- `dispatched` 表示GitHub工作流已触发，不等于收件箱已收到。
