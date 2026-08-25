"""Generate and send the noon report from fundamental and technical scores."""

from __future__ import annotations

import html
import json
import os
import smtplib
from email.message import EmailMessage
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from research_core import build_push_payload, freeze_weekly_plan, save_selection_snapshot


PARAMETER_LABELS = {
    "momentum_window": ("动量计算窗口", "最近多少个交易日用于计算价格动量"),
    "trend_window": ("趋势均线窗口", "用于判断价格趋势的均线天数"),
    "volatility_window": ("波动率窗口", "用于估算年化波动率的交易日数"),
    "volume_window": ("成交量均值窗口", "量比所使用的平均成交量天数"),
    "rsi_window": ("RSI窗口", "RSI强弱指标的计算天数"),
    "bollinger_window": ("布林带窗口", "布林带中轨和标准差的计算天数"),
    "atr_window": ("ATR窗口", "平均真实波幅的计算天数"),
    "selection_weights.fundamental": ("研究试验-基本面权重", "仅用于后台历史对照，不写入实际选股"),
    "selection_weights.technical": ("研究试验-技术面权重", "仅用于后台历史对照，不写入实际选股"),
    "selection_weights.board": ("研究试验-板块强度权重", "仅用于后台历史对照，不写入实际选股"),
    "selection_weights.morning_fund": ("研究试验-上午资金权重", "仅用于后台历史对照，不写入实际选股"),
}
METRIC_LABELS = {
    "annual_return": ("样本外年化收益", "样本外日收益折算后的年化收益率"),
    "max_drawdown": ("样本外最大回撤", "样本外净值从高点到低点的最大跌幅"),
    "sharpe_ratio": ("样本外夏普比率", "收益相对波动风险的比值"),
}


def _number(value, digits=1, suffix="") -> str:
    try:
        return f"{float(value):.{digits}f}{suffix}"
    except (TypeError, ValueError):
        return "--"


def _change_value(part: str, value) -> str:
    if value is None:
        return "首次启用"
    if "weights." in part:
        return _number(float(value) * 100, 0, "%")
    return str(value)


def _plan_text(item: dict) -> str:
    plan = item.get("morning_plan") or {}
    if not plan.get("levels_available"):
        return f"{plan.get('status') or '暂无上午盘价位'}：{plan.get('reason') or '等待数据'}"
    entry = plan.get("entry_zone") or {}
    stop = plan.get("stop_zone") or {}
    targets = plan.get("take_profit_zones") or []
    first = targets[0] if targets else {}
    second = targets[1] if len(targets) > 1 else {}
    return (
        f"进场{_number(entry.get('low'), 2)}-{_number(entry.get('high'), 2)}；"
        f"突破{_number(plan.get('breakout_trigger'), 2)}；禁追>{_number(plan.get('max_chase_price'), 2)}；"
        f"止损{_number(stop.get('low'), 2)}-{_number(stop.get('high'), 2)}；"
        f"止盈一{_number(first.get('low'), 2)}-{_number(first.get('high'), 2)}；"
        f"止盈二{_number(second.get('low'), 2)}-{_number(second.get('high'), 2)}；"
        f"{plan.get('execution_state') or plan.get('status', '')}"
    )


def build_email(payload: dict) -> EmailMessage:
    observations = payload.get("observations", [])
    plain_rows = []
    html_rows = []
    for item in observations:
        primary_board = item.get("primary_board") or {}
        decision = item.get("trade_decision") or {}
        stock_flow = item.get("intraday_fund_flow") or {}
        plan_text = _plan_text(item)
        selection_score = item.get("selection_score", item.get("combined_score"))
        plain_rows.append(
            f"{item.get('rank', '-')}. {item.get('code')} {item.get('name', '')} | "
            f"基本面{_number(item.get('fundamental_score'), 0)}→行业校准{_number(item.get('sector_adjusted_fundamental_score'), 0)} | "
            f"技术研究{_number(item.get('technical_score'), 1)}（不计入综合分） | "
            f"综合选股{_number(selection_score, 1)} | "
            f"板块{primary_board.get('name') or '未匹配'}({_number(item.get('board_strength_score'), 0)}) | "
            f"个股主力净占比{_number(stock_flow.get('main_net_pct'), 2, '%')} | "
            f"结论{decision.get('status', '等待确认')}\n  {plan_text}"
        )
        html_rows.append(
            "<tr>"
            f'<td width="7%" style="width:7%;padding:10px 6px;border-bottom:1px solid #e4e9f0;text-align:center;vertical-align:middle">{item.get("rank", "-")}</td>'
            f'<td width="22%" style="width:22%;padding:10px 8px;border-bottom:1px solid #e4e9f0;text-align:left;vertical-align:middle;word-break:break-word"><strong>{html.escape(str(item.get("code", "")))}</strong><br>{html.escape(str(item.get("name", "")))}</td>'
            f'<td width="23%" style="width:23%;padding:10px 8px;border-bottom:1px solid #e4e9f0;text-align:left;vertical-align:middle;word-break:break-word">{html.escape(str(item.get("selection_industry") or item.get("industry") or "行业待刷新"))}</td>'
            f'<td width="16%" style="width:16%;padding:10px 6px;border-bottom:1px solid #e4e9f0;text-align:center;vertical-align:middle;white-space:nowrap">{_number(item.get("fundamental_score"), 0)}→{_number(item.get("sector_adjusted_fundamental_score"), 0)}</td>'
            f'<td width="16%" style="width:16%;padding:10px 6px;border-bottom:1px solid #e4e9f0;text-align:center;vertical-align:middle;white-space:nowrap">{_number(item.get("technical_score"), 1)}<br><small>仅研究</small></td>'
            f'<td width="16%" style="width:16%;padding:10px 6px;border-bottom:1px solid #e4e9f0;text-align:center;vertical-align:middle;white-space:nowrap"><strong>{_number(selection_score, 1)}</strong></td>'
            "</tr>"
            '<tr><td colspan="6" style="padding:8px 10px 13px;border-bottom:2px solid #cfd8e5;background:#f8fafc;line-height:1.7">'
            f'<strong>{html.escape(str(decision.get("status") or "等待确认"))}</strong> · '
            f'板块：{html.escape(str(primary_board.get("name") or "未匹配"))} / 强度{_number(item.get("board_strength_score"), 0)} · '
            f'个股主力净占比：{_number(stock_flow.get("main_net_pct"), 2, "%")}<br>'
            f'<span style="color:#475569">{html.escape(plan_text)}</span><br>'
            f'<span style="font-size:11px;color:#64748b">综合分：{html.escape(str(item.get("selection_score_explanation") or "按基本面、板块和上午资金计算"))}。量化因子不参与选股；ATR {_number((item.get("atr_pct") or 0) * 100, 2, "%")}仅辅助止损距离。</span>'
            '</td></tr>'
        )

    rules = payload.get("rules", {})
    technical = payload.get("technical_summary", {})
    metadata = technical.get("metadata", {})
    metrics = technical.get("oos_metrics", {})
    market = payload.get("market", {})
    breadth = market.get("market_stats") or market.get("breadth") or market.get("stats") or market
    external = payload.get("external_market") or {}
    capital = payload.get("capital_strength") or {}
    boards = payload.get("rotation_boards") or []
    hot_core = payload.get("hot_core_candidates") or []
    weekly = payload.get("weekly_plan") or {}
    account = payload.get("account") or weekly.get("account") or {}
    holding_actions = weekly.get("holding_actions") or []
    validation = technical.get("latest_validation") or {}
    optimization = technical.get("optimization_log_entry") or {}
    model_gate = payload.get("quant_model_gate") or rules.get("quant_model_gate") or {}
    selection_weights = rules.get("selection_weights") or {}
    selection_weight_text = "、".join(
        f"{label}{_number(float(selection_weights.get(key) or 0) * 100, 0, '%')}"
        for key, label in (("fundamental", "基本面"), ("technical", "技术面"), ("board", "板块"), ("morning_fund", "上午资金"))
    )
    parameter_changes = optimization.get("parameter_changes") or []
    metric_changes = optimization.get("metric_changes") or []
    parameter_change_plain = [
        f"{item.get('label') or PARAMETER_LABELS.get(str(item.get('part')), (item.get('part'), ''))[0]}："
        f"{_change_value(str(item.get('part')), item.get('before'))} → {_change_value(str(item.get('part')), item.get('after'))}；"
        f"含义：{item.get('meaning') or PARAMETER_LABELS.get(str(item.get('part')), ('', '暂无释义'))[1]}"
        for item in parameter_changes
    ]
    metric_change_plain = [
        f"{item.get('label') or METRIC_LABELS.get(str(item.get('metric')), (item.get('metric'), ''))[0]}："
        f"{item.get('before')} → {item.get('after')}（变化{float(item.get('delta') or 0):+.4f}）；"
        f"含义：{item.get('meaning') or METRIC_LABELS.get(str(item.get('metric')), ('', '暂无释义'))[1]}"
        for item in metric_changes
    ]
    external_plain = [
        f"{item.get('name')} {float(item.get('change_pct') or 0):+.2f}%（{item.get('as_of') or '时间未知'}）"
        for item in (external.get("markets") or [])
    ]
    event_plain = [
        f"{item.get('name') or item.get('event')}：{item.get('impact_summary') or '等待A股资金确认'}"
        for item in (external.get("events") or [])
    ]
    board_plain = [
        f"{board.get('rank')}. {board.get('name')}({board.get('type')}) "
        f"资金{_number(board.get('main_net_inflow'), 2)}亿/强度{_number(board.get('rotation_score'), 0)}；"
        f"{board.get('effect')}；龙头："
        + "、".join(
            f"{leader.get('name')}({leader.get('leadership_role')})"
            for leader in (board.get("leaders") or [])
        )
        for board in boards[:8]
    ]
    hot_core_plain = [
        f"{index}. {item.get('code')} {item.get('name')}（{item.get('board_name')}/{item.get('leadership_role')}） | "
        f"板块{_number(item.get('board_strength_score'), 0)} | 基本面{_number(item.get('fundamental_score'), 0)} | "
        f"量化{_number(item.get('technical_score'), 1)} | {item.get('trade_decision', {}).get('status', '等待确认')}\n"
        f"  {_plan_text(item)}"
        for index, item in enumerate(hot_core, start=1)
    ]
    weekly_plain = []
    for item in weekly.get("selections") or []:
        trend = item.get("weekly_trend") or {}
        entry = trend.get("entry_zone") or {}
        targets = trend.get("take_profit") or []
        position = item.get("position_plan") or {}
        weekly_plain.append(
            f"{item.get('role')} {item.get('code')} {item.get('name')} | {item.get('status')} | "
            f"周度分{_number(item.get('weekly_score'), 1)} | "
            f"买入{_number(entry.get('low'), 2)}-{_number(entry.get('high'), 2)} | "
            f"禁追>{_number(trend.get('max_chase_price'), 2)} | 止损{_number(trend.get('stop_price'), 2)} | "
            f"止盈{_number((targets[0] if targets else {}).get('price'), 2)}/{_number((targets[1] if len(targets) > 1 else {}).get('price'), 2)} | "
            f"{position.get('quantity', 0)}股/约{_number(position.get('estimated_value'), 0)}元/计划亏损{_number(position.get('planned_loss'), 0)}元"
            + (f"\n  阻断：{'；'.join(position.get('reasons') or [])}" if position.get("reasons") else "")
        )
    holding_plain = [
        f"{item.get('code')} {item.get('name')} | 持有{item.get('quantity', 0)}股/可卖{item.get('available_quantity', 0)}股 | "
        f"成本{_number(item.get('cost_price'), 4)}/参考收盘{_number(item.get('reference_price'), 2)} | "
        f"浮动{_number(item.get('pnl_pct'), 2, '%')} | 建议：{item.get('action')}"
        + (f" {item.get('sell_quantity')}股" if item.get("sell_quantity") else "")
        + f"；{item.get('reason')}；结构止损{_number(item.get('stop_price'), 2)}"
        for item in holding_actions
    ]
    scenarios_plain = [
        f"{row.get('name')}：{row.get('summary')}；触发：{'、'.join(row.get('triggers') or [])}"
        for row in weekly.get("event_scenarios") or []
    ]
    empty_text = "今日没有股票达到基本面、板块和盘中条件，保留空观察池。"
    site_url = os.getenv("SITE_URL", "https://a-share-trading.onrender.com")
    plain = (
        f"{payload.get('subject')}\n"
        f"生成时间：{payload.get('generated_at')}\n"
        f"分析窗口：{payload.get('analysis_window')}\n"
        f"使用时段：{payload.get('execution_window')}\n\n"
        "账户风险闸门\n"
        f"净值{_number(account.get('equity'), 0)}元；上周盈亏{_number(account.get('last_week_pnl'), 0)}元/"
        f"{_number(account.get('last_week_return_pct'), 2, '%')}；风险档位{(account.get('risk_profile') or {}).get('name', '--')}；"
        f"总仓上限{_number((account.get('risk_profile') or {}).get('max_total_pct', 0) * 100, 0, '%')}；"
        f"是否允许开仓：{'是' if account.get('can_open_new') else '否'}。\n"
        + ("阻断原因：" + "；".join(account.get("block_reasons") or []) + "\n" if account.get("block_reasons") else "")
        + "\n午间持仓建议（结合前一交易日与当日上午行情）\n"
        + ("\n".join(holding_plain) if holding_plain else "尚未确认逐股持仓，或持仓行情数据不足。")
        + "\n本周固定名单\n"
        + ("\n".join(weekly_plain) if weekly_plain else "本周没有股票同时通过基本面、周趋势、板块、事件和风险预算，保持空仓。")
        + "\n\n国际事件三情景\n"
        + ("\n".join(scenarios_plain) if scenarios_plain else "事件数据不足，按不确定情景控制仓位。")
        + "\n\n仓位边界：计划股数是单股目标上限，不代表忽略现有仓位后的追加买入量；执行前自行核对实际总仓位。\n\n"
        "市场概况\n"
        f"上涨：{breadth.get('up', '--')}；下跌：{breadth.get('down', '--')}；"
        f"涨停：{breadth.get('limit_up', '--')}；跌停：{breadth.get('limit_down', '--')}\n\n"
        "外盘、美股与事件影响\n"
        + ("\n".join(external_plain + event_plain) if external_plain or event_plain else "外盘/事件数据暂不可用")
        + "\n说明：外盘和事件只形成情景推演，必须由A股资金与板块扩散确认。\n\n"
        "每日资金强度与板块效应\n"
        f"资金强度：{capital.get('label', '--')}；强板块{capital.get('strong_board_count', 0)}个；"
        f"前三板块主力净流入合计{_number(capital.get('top_three_main_net_inflow'), 2)}亿。\n"
        + ("\n".join(board_plain) if board_plain else "暂无有效板块资金数据")
        + "\n\n热门核心观察池（强势板块龙头/次龙头）\n"
        + ("\n".join(hot_core_plain) if hot_core_plain else "本次未形成满足条件的板块龙头/次龙头")
        + "\n\n"
        "中长期实际选股观察池（量化仅研究）\n"
        + ("\n".join(plain_rows) if plain_rows else empty_text)
        + "\n\n"
        f"规则：行业校准基本面≥{rules.get('fundamental_min')}；量化因子不参与实际选股、综合排名或进场许可；"
        f"综合权重：{selection_weight_text}；"
        f"行业内相对分权重{_number(float(rules.get('industry_relative_weight') or 0) * 100, 0, '%')}，"
        f"单行业优先最多{rules.get('industry_limit', 4)}只。\n"
        f"量化信号日期：{metadata.get('signal_date', '--')}；"
        f"样本外年化：{_number(metrics.get('annual_return'), 2, '%')}；"
        f"最大回撤：{_number(metrics.get('max_drawdown'), 2, '%')}；"
        f"夏普：{_number(metrics.get('sharpe_ratio'), 2)}\n\n"
        "量化闭环\n"
        f"次日验证：{validation.get('message', '尚无验证')}；命中率{_number(validation.get('hit_rate'), 2, '%')}；"
        f"平均收益{_number(validation.get('average_return'), 3, '%')}；超额{_number(validation.get('excess_return'), 3, '%')}。\n"
        + "\n".join(optimization.get("actions") or ["今日优化日志尚未生成"])
        + "\n具体参数调整：\n" + ("\n".join(parameter_change_plain) if parameter_change_plain else "参数保持不变")
        + "\n样本外指标变化：\n" + ("\n".join(metric_change_plain) if metric_change_plain else "暂无可比较指标")
        + "\n约束：单日验证只记录，不直接改写因子定义；参数仅在预设网格内按滚动样本外结果选择。\n\n"
        "本报告给出条件价位，但不自动委托；排名不等于买点。\n"
        f"网站：{site_url}"
    )
    external_rows = "".join(
        '<tr>'
        f'<td style="padding:8px;border-bottom:1px solid #e4e9f0">{html.escape(str(item.get("name") or "--"))}</td>'
        f'<td style="padding:8px;border-bottom:1px solid #e4e9f0;text-align:center">{_number(item.get("change_pct"), 2, "%")}</td>'
        f'<td style="padding:8px;border-bottom:1px solid #e4e9f0">{html.escape(str(item.get("as_of") or "--"))}</td>'
        '</tr>'
        for item in (external.get("markets") or [])
    ) or '<tr><td colspan="3" style="padding:10px">外盘行情暂不可用。</td></tr>'
    event_rows = "".join(
        f'<div style="margin-top:8px"><strong>{html.escape(str(item.get("name") or item.get("event") or "事件"))}</strong>：'
        f'{html.escape(str(item.get("impact_summary") or "等待A股资金确认"))}</div>'
        for item in (external.get("events") or [])
    )
    board_rows = "".join(
        '<tr>'
        f'<td style="padding:8px;border-bottom:1px solid #e4e9f0">{board.get("rank")}</td>'
        f'<td style="padding:8px;border-bottom:1px solid #e4e9f0"><strong>{html.escape(str(board.get("name") or "--"))}</strong><br><span style="font-size:11px;color:#64748b">{html.escape(str(board.get("type") or ""))}</span></td>'
        f'<td style="padding:8px;border-bottom:1px solid #e4e9f0;text-align:center">{_number(board.get("main_net_inflow"), 2)}亿<br>强度{_number(board.get("rotation_score"), 0)}</td>'
        f'<td style="padding:8px;border-bottom:1px solid #e4e9f0">{html.escape(str(board.get("effect") or "--"))}</td>'
        f'<td style="padding:8px;border-bottom:1px solid #e4e9f0">{html.escape("、".join(f"{leader.get("name")}({leader.get("leadership_role")})" for leader in (board.get("leaders") or [])) or "--")}</td>'
        '</tr>'
        for board in boards[:8]
    ) or '<tr><td colspan="5" style="padding:10px">暂无有效板块资金数据。</td></tr>'
    hot_core_html = "".join(
        '<div style="padding:11px 12px;border-bottom:1px solid #f2dcc1;line-height:1.7">'
        f'<strong>{index}. {html.escape(str(item.get("code") or ""))} {html.escape(str(item.get("name") or ""))}</strong> · '
        f'{html.escape(str(item.get("board_name") or "--"))}/{html.escape(str(item.get("leadership_role") or "板块核心"))}<br>'
        f'<span style="font-size:12px;color:#7c2d12">板块 {_number(item.get("board_strength_score"), 0)} · '
        f'基本面 {_number(item.get("fundamental_score"), 0)} · 量化 {_number(item.get("technical_score"), 1)} · '
        f'{html.escape(str((item.get("trade_decision") or {}).get("status") or "等待确认"))}</span><br>'
        f'<span style="font-size:12px;color:#475569">{html.escape(_plan_text(item))}</span>'
        '</div>'
        for index, item in enumerate(hot_core, start=1)
    ) or '<div style="padding:11px 12px">本次未形成强势板块龙头观察名单。</div>'
    weekly_rows = "".join(
        '<tr>'
        f'<td style="padding:8px;border-bottom:1px solid #dbe5ef"><strong>{html.escape(str(item.get("role") or ""))}</strong><br>{html.escape(str(item.get("code") or ""))} {html.escape(str(item.get("name") or ""))}</td>'
        f'<td style="padding:8px;border-bottom:1px solid #dbe5ef">{html.escape(str(item.get("status") or ""))}<br>周度分 {_number(item.get("weekly_score"), 1)}</td>'
        f'<td style="padding:8px;border-bottom:1px solid #dbe5ef">买入 {_number((item.get("weekly_trend") or {}).get("entry_zone", {}).get("low"), 2)}-{_number((item.get("weekly_trend") or {}).get("entry_zone", {}).get("high"), 2)}<br>禁追 {_number((item.get("weekly_trend") or {}).get("max_chase_price"), 2)} · 止损 {_number((item.get("weekly_trend") or {}).get("stop_price"), 2)}</td>'
        f'<td style="padding:8px;border-bottom:1px solid #dbe5ef">{(item.get("position_plan") or {}).get("quantity", 0)}股<br>约{_number((item.get("position_plan") or {}).get("estimated_value"), 0)}元 · 风险{_number((item.get("position_plan") or {}).get("planned_loss"), 0)}元</td>'
        '</tr>'
        for item in weekly.get("selections") or []
    ) or '<tr><td colspan="4" style="padding:10px">本周没有通过全部闸门的股票，保持空仓。</td></tr>'
    holding_rows = "".join(
        '<tr>'
        f'<td style="padding:8px;border-bottom:1px solid #dbe5ef"><strong>{html.escape(str(item.get("code") or ""))} {html.escape(str(item.get("name") or ""))}</strong><br>持有{item.get("quantity", 0)}股 / 可卖{item.get("available_quantity", 0)}股</td>'
        f'<td style="padding:8px;border-bottom:1px solid #dbe5ef">成本 {_number(item.get("cost_price"), 4)}<br>参考收盘 {_number(item.get("reference_price"), 2)} / {_number(item.get("pnl_pct"), 2, "%")}</td>'
        f'<td style="padding:8px;border-bottom:1px solid #dbe5ef"><strong>{html.escape(str(item.get("action") or "等待数据"))}</strong>'
        + (f' {item.get("sell_quantity")}股' if item.get("sell_quantity") else '')
        + f'<br>{html.escape(str(item.get("reason") or ""))}<br>结构止损 {_number(item.get("stop_price"), 2)}</td>'
        '</tr>'
        for item in holding_actions
    ) or '<tr><td colspan="3" style="padding:10px">尚未确认逐股持仓，或持仓行情数据不足。</td></tr>'
    scenarios_html = "".join(
        f'<div style="margin:7px 0"><strong>{html.escape(str(row.get("name") or ""))}</strong>：{html.escape(str(row.get("summary") or ""))}</div>'
        for row in weekly.get("event_scenarios") or []
    )
    validation_html = (
        f'{validation.get("signal_date")} → {validation.get("validation_date")}：'
        f'命中率{_number(validation.get("hit_rate"), 2, "%")}，平均{_number(validation.get("average_return"), 3, "%")}，'
        f'相对全市场等权超额{_number(validation.get("excess_return"), 3, "%")}'
        if validation.get("status") == "validated" else html.escape(str(validation.get("message") or "尚无次日验证"))
    )
    optimization_html = "".join(
        f'<li style="margin:5px 0">{html.escape(str(action))}</li>'
        for action in (optimization.get("actions") or ["今日优化日志尚未生成"])
    )
    parameter_change_html = "".join(
        f'<li style="margin:5px 0">{html.escape(line)}</li>' for line in parameter_change_plain
    ) or '<li style="margin:5px 0">参数保持不变</li>'
    metric_change_html = "".join(
        f'<li style="margin:5px 0">{html.escape(line)}</li>' for line in metric_change_plain
    ) or '<li style="margin:5px 0">暂无可比较指标</li>'
    table_body = "".join(html_rows) if html_rows else f'<tr><td colspan="6">{empty_text}</td></tr>'
    body = f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"></head>
    <body style="margin:0;background:#f3f6fa;font-family:Arial,'Microsoft YaHei',sans-serif;color:#172033">
      <table role="presentation" width="100%"><tr><td align="center" style="padding:24px">
        <table role="presentation" width="760" style="max-width:100%;background:#fff;border-collapse:collapse;border:1px solid #dce3ec">
          <tr><td style="padding:24px;background:#10223f;color:white">
            <div style="font-size:12px;color:#9fc2ff">WEEKLY TREND × POSITION × RISK</div>
            <h1 style="margin:6px 0 8px;font-size:24px">{html.escape(str(payload.get('subject')))}</h1>
            <div style="color:#c8d7ea">{html.escape(str(payload.get('analysis_window')))} · {html.escape(str(payload.get('execution_window')))}</div>
          </td></tr>
          <tr><td style="padding:22px">
            <div style="padding:14px;background:#edf8f5;border-left:4px solid #16805d;line-height:1.8">
              <strong>账户风险档位：{html.escape(str((account.get('risk_profile') or {}).get('name') or '--'))}</strong> ·
              净值 {_number(account.get('equity'), 0)} 元 · 上周 {_number(account.get('last_week_return_pct'), 2, '%')} ·
              总仓上限 {_number((account.get('risk_profile') or {}).get('max_total_pct', 0) * 100, 0, '%')} ·
              新开仓：{'允许' if account.get('can_open_new') else '禁止'}<br>
              {html.escape('；'.join(account.get('block_reasons') or []) or '账户风险闸门通过')}
            </div>
            <h2 style="font-size:18px;margin-top:24px">午间持仓建议</h2>
            <p style="color:#5e6b7d">使用你确认的持仓、最新可得收盘行情、周度趋势和账户风险生成；截图识别值必须先人工确认。</p>
            <table width="100%" cellpadding="0" cellspacing="0" style="width:100%;border-collapse:collapse;font-size:12px">
              <thead><tr style="background:#edf2f8"><th style="padding:8px;text-align:left">持仓</th><th style="padding:8px;text-align:left">成本/行情</th><th style="padding:8px;text-align:left">午后参考动作</th></tr></thead>
              <tbody>{holding_rows}</tbody>
            </table>
            <h2 style="font-size:18px;margin-top:24px">本周固定名单（周内只撤销，不换排行）</h2>
            <p style="color:#5e6b7d">{html.escape(str(weekly.get('selection_policy') or ''))}</p>
            <table width="100%" cellpadding="0" cellspacing="0" style="width:100%;border-collapse:collapse;font-size:12px">
              <thead><tr style="background:#edf2f8"><th style="padding:8px;text-align:left">身份/股票</th><th style="padding:8px;text-align:left">状态</th><th style="padding:8px;text-align:left">价格计划</th><th style="padding:8px;text-align:left">仓位</th></tr></thead>
              <tbody>{weekly_rows}</tbody>
            </table>
            <h3 style="font-size:15px;margin:18px 0 6px">国际事件三情景</h3>{scenarios_html}
            <p style="padding:10px;background:#fff8e8;color:#725219">计划股数是单股目标仓位上限，不代表追加买入量；执行前请自行核对账户实际总仓位。</p>
            <div style="padding:12px;background:#eef5ff;border-left:4px solid #286fe8;line-height:1.7">
              上涨 {breadth.get('up', '--')} · 下跌 {breadth.get('down', '--')} ·
              涨停 {breadth.get('limit_up', '--')} · 跌停 {breadth.get('limit_down', '--')}
            </div>
            <h2 style="font-size:18px;margin-top:24px">外盘、美股与地缘事件影响</h2>
            <p style="color:#5e6b7d">覆盖：{html.escape(str(external.get('coverage') or '暂不可用'))}。外部变化只作情景输入，必须等待A股资金确认。</p>
            <table width="100%" cellpadding="0" cellspacing="0" style="width:100%;border-collapse:collapse;font-size:12px">
              <thead><tr style="background:#edf2f8"><th style="padding:8px;text-align:left">市场</th><th style="padding:8px">涨跌</th><th style="padding:8px;text-align:left">时间</th></tr></thead>
              <tbody>{external_rows}</tbody>
            </table>
            {event_rows}
            <h2 style="font-size:18px;margin-top:24px">每日资金强度、板块效应与龙头</h2>
            <p style="color:#5e6b7d">资金强度：<strong>{html.escape(str(capital.get('label') or '--'))}</strong> · 强板块 {capital.get('strong_board_count', 0)} 个 · 前三主力净流入合计 {_number(capital.get('top_three_main_net_inflow'), 2)} 亿</p>
            <table width="100%" cellpadding="0" cellspacing="0" style="width:100%;border-collapse:collapse;table-layout:fixed;font-size:12px">
              <thead><tr style="background:#edf2f8"><th width="6%" style="padding:8px">#</th><th width="18%" style="padding:8px;text-align:left">板块</th><th width="18%" style="padding:8px">资金/强度</th><th width="28%" style="padding:8px;text-align:left">板块效应</th><th width="30%" style="padding:8px;text-align:left">龙头/次龙头</th></tr></thead>
              <tbody>{board_rows}</tbody>
            </table>
            <h2 style="font-size:18px;margin-top:24px">热门核心观察池：强势板块龙头与次龙头</h2>
            <p style="color:#5e6b7d">保留好板块与板块核心票；量化结果仅作独立研究，不影响候选或进场结论。</p>
            <div style="border:1px solid #f2dcc1;background:#fff7ed">{hot_core_html}</div>
            <h2 style="font-size:18px;margin-top:24px">行业校准后的实际选股观察池</h2>
            <p style="color:#5e6b7d">{html.escape(str(rules.get('selection_formula') or ('综合权重：' + selection_weight_text)))}；原始基本面→行业校准分；单行业优先最多 {rules.get('industry_limit', 4)} 只。量化因子仅独立研究，不参与选股。</p>
            <table width="100%" cellpadding="0" cellspacing="0" style="width:100%;border-collapse:collapse;table-layout:fixed;font-size:13px">
              <thead><tr style="background:#edf2f8">
                <th width="7%" style="width:7%;padding:10px 6px;border-bottom:1px solid #cfd8e5;text-align:center;vertical-align:middle;white-space:nowrap">序</th>
                <th width="22%" style="width:22%;padding:10px 8px;border-bottom:1px solid #cfd8e5;text-align:left;vertical-align:middle;white-space:nowrap">股票</th>
                <th width="23%" style="width:23%;padding:10px 8px;border-bottom:1px solid #cfd8e5;text-align:left;vertical-align:middle;white-space:nowrap">行业</th>
                <th width="16%" style="width:16%;padding:10px 6px;border-bottom:1px solid #cfd8e5;text-align:center;vertical-align:middle;white-space:nowrap">基本面<br>原始→校准</th>
                <th width="16%" style="width:16%;padding:10px 6px;border-bottom:1px solid #cfd8e5;text-align:center;vertical-align:middle;white-space:nowrap">技术量化<br>仅研究</th>
                <th width="16%" style="width:16%;padding:10px 6px;border-bottom:1px solid #cfd8e5;text-align:center;vertical-align:middle;white-space:nowrap">综合选股</th>
              </tr></thead>
              <tbody>{table_body}</tbody>
            </table>
            <h2 style="font-size:18px;margin-top:24px">量化样本外表现</h2>
            <p>信号日期 {metadata.get('signal_date', '--')} · 年化 {_number(metrics.get('annual_return'), 2, '%')} ·
            最大回撤 {_number(metrics.get('max_drawdown'), 2, '%')} · 夏普 {_number(metrics.get('sharpe_ratio'), 2)}</p>
            <h2 style="font-size:18px;margin-top:24px">量化次日验证与每日优化日志</h2>
            <p>{validation_html}</p><ul style="padding-left:20px">{optimization_html}</ul>
            <h3 style="font-size:15px;margin-bottom:6px">具体参数调整</h3><ul style="padding-left:20px">{parameter_change_html}</ul>
            <h3 style="font-size:15px;margin-bottom:6px">样本外指标变化</h3><ul style="padding-left:20px">{metric_change_html}</ul>
            <p style="font-size:12px;color:#64748b">{html.escape(str(optimization.get('guardrail') or '单日验证只记录，不直接改写因子定义。'))}</p>
            <div style="margin-top:22px;padding:12px;background:#fff7ed;border-left:4px solid #f59e0b;color:#7c2d12">
              价位是条件计划，不自动委托；板块、基本面和午后触发未同时通过时，不进入可执行观察。量化仅独立优化，回测表现不保证未来收益。
            </div>
            <p style="margin-top:22px"><a href="{html.escape(site_url, quote=True)}">打开三核研究网站</a></p>
          </td></tr>
        </table>
      </td></tr></table>
    </body></html>"""
    message = EmailMessage()
    message["Subject"] = str(payload.get("subject"))
    message.set_content(plain)
    message.add_alternative(body, subtype="html")
    return message


def _recipients() -> list[str]:
    return [address.strip() for address in os.environ["MAIL_TO"].replace(";", ",").split(",") if address.strip()]


def _send_email_via_brevo(message: EmailMessage, api_key: str) -> None:
    username = os.environ["MAIL_USERNAME"]
    recipients = _recipients()
    if not recipients:
        raise ValueError("MAIL_TO 未配置有效的收件邮箱")
    payload = {
        "sender": {"name": os.getenv("MAIL_FROM_NAME", "A股双评分报告"), "email": os.getenv("BREVO_SENDER_EMAIL", os.getenv("MAIL_FROM", username))},
        "to": [{"email": address} for address in recipients],
        "subject": str(message["Subject"]),
        "textContent": message.get_body(preferencelist=("plain",)).get_content(),
        "htmlContent": message.get_body(preferencelist=("html",)).get_content(),
    }
    request = Request(
        "https://api.brevo.com/v3/smtp/email",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"accept": "application/json", "api-key": api_key, "content-type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=30) as response:
            if response.status not in (200, 201, 202):
                raise RuntimeError(f"Brevo API 返回 HTTP {response.status}")
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"Brevo API 返回 HTTP {exc.code}: {detail}") from exc


def _send_email_via_smtp(message: EmailMessage) -> None:
    username = os.environ["MAIL_USERNAME"]
    recipients = _recipients()
    if not recipients:
        raise ValueError("MAIL_TO 未配置有效的收件邮箱")
    message["From"] = os.getenv("MAIL_FROM", username)
    message["To"] = ", ".join(recipients)
    host, port = os.getenv("MAIL_SMTP_HOST", "smtp.qq.com"), int(os.getenv("MAIL_SMTP_PORT", "465"))
    if port == 465:
        with smtplib.SMTP_SSL(host, port, timeout=30) as smtp:
            smtp.login(username, os.environ["MAIL_PASSWORD"])
            smtp.send_message(message)
    else:
        with smtplib.SMTP(host, port, timeout=30) as smtp:
            smtp.starttls()
            smtp.login(username, os.environ["MAIL_PASSWORD"])
            smtp.send_message(message)


def send_email(message: EmailMessage) -> None:
    api_key = os.getenv("BREVO_API_KEY", "").strip()
    _send_email_via_brevo(message, api_key) if api_key else _send_email_via_smtp(message)


def main() -> None:
    universe_limit = int(os.getenv("EMAIL_UNIVERSE_LIMIT", "500"))
    payload = build_push_payload(refresh=True, universe_limit=universe_limit)
    factor_count = int(payload.get("technical_summary", {}).get("factor_count") or 0)
    if factor_count <= 0:
        raise RuntimeError("技术面快照缺失，停止发送，避免把数据缺失误报为无交集")
    save_selection_snapshot(payload)
    freeze_weekly_plan(payload)
    send_email(build_email(payload))
    print(f"已发送周度趋势计划：{(payload.get('weekly_plan') or {}).get('active_count', 0)} 只固定候选。")


if __name__ == "__main__":
    main()
