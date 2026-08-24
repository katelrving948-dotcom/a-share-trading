# A股周度趋势与风险系统

系统面向盯盘时间有限、账户约5万元、偏周度趋势持仓的使用方式。实际执行链为：上周完整行情与最新披露 → 基本面/资产负债风险 → 周趋势与相对强度 → 板块和国际事件三情景 → 账户风险预算 → 每周固定名单。量化因子保留独立回测和优化，但研究排行不再等于交易许可。

## 启动网站

```bash
pip install -r requirements-quant.txt
python server.py
```

访问 `http://localhost:5000`。网站包含：

1. **周度计划**：账户风险档位、2只主选/1只备选、目标股数上限、禁追、止损、止盈和国际事件三情景。
2. **基本面评分**：质量35%、成长30%、估值20%、现金流15%的透明评分和财报证据。
3. **技术面量化**：七项价量因子、滚动样本外回测和次日验证；仅作为独立研究通道。

## 周度规则

- 每周一08:00生成计划，周内使用同一 `plan_id`；名单只允许撤销，不因日度排行变化新增股票。
- 每周最多2只主选、1只备选；不满足条件时允许少于3只或空仓。
- 周度分为基本面40%、中期趋势30%、板块15%、国际事件敏感度10%、估值/拥挤度5%。中期趋势直接使用日K/周结构和沪深300相对强度，不读取量化优化分数。
- 价格明显偏离20日均线、20日涨幅过大、结构止损距离不在4%-7%、板块不足55分或财务硬风险时，不进入固定名单。
- 每只股票给出买入区、禁追价、结构止损、1.5R/2.5R止盈、5日时间止损和100股整数倍仓位。

## 账户风险

账户状态保存在已忽略的本地文件 `output/research/account_state.json`；字段模板见 `docs/account_state.example.json`。网站支持手动填写持仓，或把券商截图发送至阿里云百炼通义千问视觉模型生成待确认草稿；网站不保存原图，识别结果必须人工确认后才能保存。生产邮件从私密 `ACCOUNT_STATE_JSON` 读取确认后的账户和逐股持仓。核心字段：

```json
{
  "equity": 50000,
  "available_cash": 50000,
  "last_week_pnl": -3500,
  "last_week_end": "2026-08-21",
  "current_week_pnl": 0,
  "market_state": "普通"
}
```

- 上周亏损达到2%后，下一周进入恢复期：总仓30%、单股15%、单笔风险200元。
- 普通期总仓最高60%、单股20%、单笔风险300元；强势期最高70%。
- 本周亏损达到2%立即停止新增风险。
- 股数按 `floor[单笔风险预算 ÷ (买入中值 - 止损价) ÷ 100] × 100` 计算，并受单股、总仓和现金上限约束。
- 计划股数是该股票的目标持仓上限，不是忽略现有仓位后的追加买入量；执行前需要自行确认账户总仓位不超过当前档位上限。

账户接口均需 `Authorization: Bearer <CRON_SECRET>`：`GET/POST /api/account` 读取或更新账户，`POST /api/account/extract` 识别截图草稿，`GET /api/account/analysis` 返回私密持仓和次日建议。公开 `/api/push/preview` 不返回资金、盈亏、成本、数量或持仓动作。截图识别需要 Render 私密环境变量 `DASHSCOPE_API_KEY`；`DASHSCOPE_VISION_MODEL` 默认 `qwen3-vl-plus`，中国大陆默认接口为 `https://dashscope.aliyuncs.com/compatible-mode/v1`，使用业务空间专属地址时可通过 `DASHSCOPE_BASE_URL` 覆盖。保存时会尝试用 `GITHUB_ACTIONS_TOKEN` 更新仓库加密 Secret `ACCOUNT_STATE_JSON`，供下一次邮件任务读取；令牌权限不足时页面会明确显示仅保存到当前 Render 实例。

## 每日推送链路

`.github/workflows/daily-stock-email.yml` 在工作日00:00 UTC（北京时间08:00）运行。周度名单仍只在周一生成并于周内冻结；逐股持仓建议每天使用最新可得收盘数据刷新：

`GitHub Actions → 私密持仓 → 最新收盘分析 → 冻结weekly_plan.json → 邮件服务`

邮件和网站都消费 `research_core.build_push_payload()` 的同一份 `weekly_plan`，不各自计算仓位或交易许可。`dispatched` 只说明工作流已触发，不等于收件箱已收到。

需要的 Secrets：`MAIL_USERNAME`、`MAIL_PASSWORD`、`MAIL_TO`，以及推荐的 `ACCOUNT_STATE_JSON`。Render任务触发仍使用 `CRON_SECRET`、`GITHUB_ACTIONS_TOKEN`。

## 数据与证据边界

- 基本面四维评分来自东方财富已披露财务指标；最终周度候选额外读取资产负债率、货币资金、应收和存货。
- 综合快讯用于风险词匹配，但未匹配不代表交易所公告已完整核验；页面保留该边界。
- 外盘、商品和地缘事件只形成基准/利好/利空情景，必须由A股板块趋势和个股相对强度确认。
- 止损价不是保证成交价；跳空、跌停、停牌和流动性不足可能扩大实际亏损。
- 做T默认关闭；只有已确认底仓、周趋势有效、未触发周熔断且已确认券商条件单时，才显示资格提示，系统仍不自动下单。

## 验证

```bash
python -m unittest discover -s tests -v
python -m py_compile server.py research_core.py weekly_strategy.py fundamental.py email_digest.py data_feed.py selection_model.py
```

研究结果不构成收益承诺或个股买卖指令。
