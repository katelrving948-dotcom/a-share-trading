# 周度趋势系统架构

生产入口为 `python server.py`，网页为 `templates/index.html`。`research_core.py` 负责统一编排，`weekly_strategy.py` 是唯一的周度名单、账户仓位和持仓动作规则源；邮件与网页不得重复推导交易许可。

| 模块 | 输入 | 输出 |
| --- | --- | --- |
| `fundamental.py` | 行情初筛、已披露财务指标 | 四维基本面评分与证据日期 |
| `data_feed.py` | 行情、K线、板块、外盘、新闻、财务与资产负债表API | 原始事实数据及来源状态 |
| `weekly_strategy.py` | 账户级净值/周盈亏、日K、沪深300、板块、事件、基本面风险 | 风险档位、周趋势、目标仓位上限、固定名单 |
| `research_core.py` | 基本面快照、市场研究、技术研究、周度模块 | 网站/邮件统一载荷 |
| `email_digest.py` | 统一载荷 | 周度纯文本/HTML邮件并冻结计划 |
| `quant_*.py` | OHLCV、历史研究快照 | 独立量化回测、次日验证和优化日志 |
| `server.py` | 浏览器与受保护任务请求 | 网站、账户接口和任务调度 |

## 状态文件

- `output/research/account_state.json`：本地账户级状态；当前只使用净值和周盈亏，生产优先使用私密 `ACCOUNT_STATE_JSON`。
- `output/research/weekly_plan.json`：当前周冻结名单。公开同步版本会移除逐笔持仓，只保留风险摘要。
- `output/research/fundamental_latest.json`：基本面快照。
- `output/research/selection_snapshot.json`：日度研究快照，仅供量化历史对照。

## 关键不变量

1. 同一ISO周内 `plan_id` 不变；新排行不能替换已冻结代码。
2. 周内只允许撤销，只有预先列出的备选股可在主选撤销后启用。
3. 本周回撤达到2%，或100股最小单位超过单股仓位/风险预算时，不允许新增风险。
4. 日度研究状态最多显示“日度条件满足（非交易许可）”；只有周度名单可以显示“可执行”。
5. 精确股数由单笔风险预算反推，邮件和网页只展示后端结果。
6. 量化优化不自动改写实际周度权重或交易许可。
7. 外部事件必须保留来源/时间/情景边界，不能直接断言A股方向。

## API

- `GET /api/status`
- `GET /api/push/status`
- `GET /api/push/preview`
- `POST /api/push/run`
- `POST /api/cron/daily-email`（旧路径兼容，实际为周一计划）
- `GET /api/cron/daily-email/status`
- `GET /api/account`
- `POST /api/account`（需要CRON_SECRET）
- `GET /api/fundamental`
- `POST /api/fundamental/run`
- `GET /api/technical`
- `POST /api/technical/sync`（需要CRON_SECRET）

## 失败关闭

- K线不足、财务数据冲突或价格无效时，不生成可执行计划。
- 资产负债表缺失时明确标注未核验，不伪造债务指标。
- 当前不采集逐股持仓，输出股数仅表示目标仓位上限；执行前由用户自行核对实际总仓位。
- 跳空和跌停风险只做压力说明，止损价不表述为最大可能损失。
