"""Generate and email the daily medium/long-term A-share shortlist."""

from __future__ import annotations

import html
import json
import os
import smtplib
from datetime import datetime
from email.message import EmailMessage
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from data_feed import DataFeed
from ai_advisor import AIAdvisor
from fundamental import LongTermFundamentalScreener, build_trade_decision


SHANGHAI = ZoneInfo("Asia/Shanghai")


def _number(value, digits=1, suffix="") -> str:
    try:
        return f"{float(value):.{digits}f}{suffix}"
    except (TypeError, ValueError):
        return "--"


def _decision(item: dict) -> dict:
    return item.get("trade_decision") or build_trade_decision(item)


def _gate_text(decision: dict) -> str:
    board = decision.get("board") or {}
    stock = decision.get("stock") or {}
    entry = decision.get("entry") or {}
    return (
        f"板块{_number(board.get('score'), 0)}"
        f"/{'过' if board.get('passed') else '否'}，"
        f"个股{_number(stock.get('score'), 0)}"
        f"/{'过' if stock.get('passed') else '否'}，"
        f"入场{_number(entry.get('score'), 0)}"
        f"/{'过' if entry.get('passed') else '否'}"
    )


def _build_discipline_digest(summary: dict, candidates: list) -> tuple[str, str]:
    discipline = summary.get("push_discipline") or {}
    capital = discipline.get("reference_capital", 50000)
    allowed_loss = discipline.get("allowed_loss_per_trade", 250)
    max_position = discipline.get("max_single_position", 10000)
    daily_loss = discipline.get("max_daily_loss", 500)
    weekly_loss = discipline.get("max_weekly_loss", 1000)
    max_new = discipline.get("max_new_trades_per_day", 1)
    all_candidates = {}
    for item in list(candidates) + list(summary.get("hot_core_candidates") or []):
        key = str(item.get("code") or id(item))
        all_candidates[key] = item
    executable = sum(
        1 for item in all_candidates.values()
        if _decision(item).get("status") == "可执行观察"
    )
    conclusion = (
        f"三道门槛全部通过 {executable} 只；"
        + ("其余候选只观察，不下单。" if executable else "今天允许空仓，不因榜单排名强行交易。")
    )
    plain = (
        f"交易纪律：参考本金{_number(capital, 0)}元，单笔计划亏损上限"
        f"{_number(allowed_loss, 0)}元，单票计划仓位不超过{_number(max_position, 0)}元；"
        f"单日亏损{_number(daily_loss, 0)}元或单周亏损{_number(weekly_loss, 0)}元停止新开仓；"
        f"每天最多新开{max_new}只，不做浮盈加仓。{conclusion}"
    )
    section = f"""
      <div style="margin:14px 0;padding:13px 14px;background:#fff7ed;border:1px solid #fdba74;border-radius:8px">
        <div style="font-size:11px;color:#c2410c;font-weight:800;letter-spacing:.5px">先看纪律，再看候选</div>
        <div style="font-size:15px;font-weight:800;margin-top:4px">{html.escape(conclusion)}</div>
        <div style="font-size:12px;color:#7c2d12;line-height:1.7;margin-top:5px">
          参考本金 {_number(capital, 0)} 元 · 单笔计划亏损上限 {_number(allowed_loss, 0)} 元 ·
          单票仓位上限 {_number(max_position, 0)} 元<br>
          单日亏损 {_number(daily_loss, 0)} 元停止 · 单周亏损 {_number(weekly_loss, 0)} 元停止 ·
          每天最多新开 {max_new} 只 · 禁止浮盈临时加仓
        </div>
      </div>
    """
    return plain, section


def select_candidates(universe_limit: int, count: int) -> tuple[list, dict]:
    screener = LongTermFundamentalScreener(data_feed=DataFeed())
    advisor = AIAdvisor()
    ranked = screener.screen(
        universe_limit=universe_limit,
        ai_advisor=advisor if advisor.is_configured else None,
    )
    recommended = [item for item in ranked if item.get("recommendation_rank")]
    recommended.sort(key=lambda item: item["recommendation_rank"])
    if not recommended:
        recommended = sorted(
            ranked,
            key=lambda item: item.get("selection_score", 0),
            reverse=True,
        )
    return recommended[:count], screener.summary


def _build_rotation_digest(summary: dict) -> tuple[str, str, str]:
    analysis = summary.get("rotation_analysis") or {}
    boards = summary.get("rotation_boards") or []
    ai_used = bool(analysis.get("available"))
    external = analysis.get("external_market") or {}
    mode = "AI+外部联动" if ai_used else (
        "资金+外部规则" if external.get("available") else "资金规则轮动"
    )
    market_stage = analysis.get("market_stage") or (
        "按板块资金强度排序" if boards else "暂无有效板块资金数据"
    )
    stage_reason = analysis.get("market_stage_reason") or analysis.get("reason") or ""
    short_outlook = analysis.get("short_term_outlook") or "等待后续交易日资金确认"
    medium_outlook = analysis.get("medium_term_outlook") or "以资金持续性和板块扩散度为准"

    plain_lines = [
        "资金流动与板块轮动",
        f"模式：{mode}；市场阶段：{market_stage}",
        f"1-3日展望：{short_outlook}",
        f"3-10日展望：{medium_outlook}",
    ]
    if stage_reason:
        plain_lines.append(f"阶段依据：{stage_reason}")
    external_summary = analysis.get("external_driver_summary") or external.get("coverage") or ""
    if external_summary:
        plain_lines.append(f"外盘联动：{external_summary}")
    market_chips = [
        f"{item.get('name', item.get('symbol', '--'))} "
        f"{_number(item.get('change_pct'), 2, '%')}"
        f"（{item.get('as_of') or '时间待核验'}）"
        for item in external.get("markets", [])
    ]
    if market_chips:
        plain_lines.append("外盘快照：" + "；".join(market_chips))

    event_rows = []
    for event in (external.get("events") or [])[:4]:
        first = (event.get("headlines") or [{}])[0]
        headline = first.get("title") or event.get("name") or "外部事件"
        source_time = " / ".join(
            part for part in (first.get("source"), first.get("time")) if part
        )
        impact = event.get("impact_summary") or "等待A股资金确认"
        plain_lines.append(
            f"外部事件：{headline}（{source_time or '时间来源待核验'}）；A股映射：{impact}"
        )
        link = html.escape(str(first.get("url") or ""), quote=True)
        headline_html = html.escape(str(headline))
        if link:
            headline_html = (
                f'<a href="{link}" style="color:#2563eb;text-decoration:none">'
                f'{headline_html}</a>'
            )
        event_rows.append(
            '<div style="padding:7px 9px;margin-top:5px;background:#f8fafc;'
            'border-left:3px solid #2563eb;font-size:12px;line-height:1.55">'
            f'<strong>{html.escape(str(event.get("name") or "外部事件"))}：</strong>'
            f'{headline_html}<div style="color:#64748b">{html.escape(source_time)} · '
            f'{html.escape(str(impact))}</div></div>'
        )

    board_rows = []
    for index, board in enumerate(boards, start=1):
        recent_flow = board.get("recent_main_net_inflow")
        recent_text = _number(recent_flow, 2, "亿") if recent_flow is not None else "--"
        state = board.get("ai_state") or "资金领先"
        confidence = board.get("ai_confidence") or "规则"
        trigger = board.get("ai_trigger") or "关注资金持续流入"
        invalidation = board.get("ai_invalidation") or "资金转为流出"
        plain_lines.append(
            f"{index}. {board.get('name', '--')}({board.get('type', '--')})："
            f"当日{_number(board.get('main_net_inflow'), 2, '亿')}，"
            f"近5日{recent_text}，轮动{_number(board.get('rotation_score'), 0)}分，"
            f"{state}/{confidence}；资金分{_number(board.get('rule_flow_score'), 0)}，"
            f"外部分{_number(board.get('external_score'), 0)}"
        )
        board_rows.append(
            '<tr style="border-top:1px solid #e7ebf3">'
            f'<td style="padding:10px 8px;color:#64748b">{index}</td>'
            f'<td style="padding:10px 8px"><strong>{html.escape(str(board.get("name", "--")))}</strong>'
            f'<div style="font-size:12px;color:#64748b">{html.escape(str(board.get("type", "--")))} · '
            f'{_number(board.get("change_pct"), 2, "%")}</div></td>'
            f'<td style="padding:10px 8px"><strong>{_number(board.get("main_net_inflow"), 2, "亿")}</strong>'
            f'<div style="font-size:12px;color:#64748b">近5日 {html.escape(recent_text)}</div></td>'
            f'<td style="padding:10px 8px;text-align:center"><strong>{_number(board.get("rotation_score"), 0)}</strong>'
            f'<div style="font-size:12px;color:#64748b">资金{_number(board.get("rule_flow_score"), 0)} · 外部{_number(board.get("external_score"), 0)}</div></td>'
            f'<td style="padding:10px 8px"><strong>{html.escape(str(state))} / {html.escape(str(confidence))}</strong>'
            f'<div style="font-size:12px;color:#64748b">触发：{html.escape(str(trigger))}；'
            f'失效：{html.escape(str(invalidation))}</div>'
            f'<div style="font-size:11px;color:#2563eb;margin-top:3px">外部依据：'
            f'{html.escape("、".join(board.get("external_reasons", [])) or "无直接映射")}</div></td>'
            "</tr>"
        )

    if not board_rows:
        board_rows.append('<tr><td colspan="5" style="padding:14px">本次未取得有效板块资金数据。</td></tr>')

    paths = []
    for path in (analysis.get("rotation_path") or [])[:3]:
        if not isinstance(path, dict):
            continue
        text = (
            f"{path.get('from', '--')} → {path.get('to', '--')}："
            f"{path.get('driver', '等待确认')}（{path.get('confidence', '低')}）"
        )
        paths.append(text)
    if paths:
        plain_lines.append("轮动路径：" + "；".join(paths))

    risks = [str(risk) for risk in (analysis.get("risks") or [])[:3] if risk]
    if risks:
        plain_lines.append("轮动风险：" + "；".join(risks))

    path_html = "".join(f"<li>{html.escape(path)}</li>" for path in paths)
    risk_html = "".join(f"<li>{html.escape(risk)}</li>" for risk in risks)
    overview_html = f"""
      <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="margin-bottom:10px">
        <tr>
          <td width="33%" style="padding:10px;background:#eef4ff;border-radius:8px;vertical-align:top">
            <div style="font-size:11px;color:#64748b">市场阶段 · {html.escape(mode)}</div>
            <div style="font-size:15px;font-weight:700;margin-top:4px">{html.escape(str(market_stage))}</div>
          </td>
          <td width="2%"></td>
          <td width="32%" style="padding:10px;background:#f2f8f5;border-radius:8px;vertical-align:top">
            <div style="font-size:11px;color:#64748b">未来1–3日</div>
            <div style="font-size:13px;font-weight:600;margin-top:4px">{html.escape(str(short_outlook))}</div>
          </td>
          <td width="2%"></td>
          <td width="31%" style="padding:10px;background:#fff6ea;border-radius:8px;vertical-align:top">
            <div style="font-size:11px;color:#64748b">未来3–10日</div>
            <div style="font-size:13px;font-weight:600;margin-top:4px">{html.escape(str(medium_outlook))}</div>
          </td>
        </tr>
      </table>
      {f'<div style="margin:6px 2px 5px;font-size:12px;color:#475569"><strong>阶段依据：</strong>{html.escape(str(stage_reason))}</div>' if stage_reason else ''}
      {f'<div style="margin:5px 2px 9px;font-size:12px;color:#2563eb"><strong>外盘联动：</strong>{html.escape(str(external_summary))}<br>{html.escape("；".join(market_chips))}</div>' if external_summary or market_chips else ''}
      {''.join(event_rows)}
    """
    detail_html = f"""
      <div style="margin-top:24px;padding-top:16px;border-top:2px solid #172033">
        <div style="font-size:11px;letter-spacing:1px;color:#2563eb;font-weight:700">SECTOR ROTATION</div>
        <div style="margin-top:3px;margin-bottom:5px;font-size:20px;font-weight:800;color:#172033">板块轮动深度解读</div>
        <div style="font-size:12px;color:#64748b;margin-bottom:10px">完整保留资金强度、轮动评分、状态、触发条件和失效条件</div>
      </div>
      <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="border:1px solid #e7ebf3;border-radius:8px;border-collapse:separate;font-size:13px">
        <thead><tr style="background:#f8fafc;color:#64748b;font-size:11px">
          <th style="padding:8px;text-align:left">#</th><th style="padding:8px;text-align:left">板块</th>
          <th style="padding:8px;text-align:left">当日 / 近5日主力净流入</th>
          <th style="padding:8px;text-align:center">评分</th><th style="padding:8px;text-align:left">判断</th>
        </tr></thead>
        <tbody>{''.join(board_rows)}</tbody>
      </table>
      {f'<div style="margin-top:8px;font-size:12px;color:#475569"><strong>轮动路径：</strong>{"；".join(html.escape(path) for path in paths)}</div>' if path_html else ''}
      {f'<div style="margin-top:5px;font-size:12px;color:#b45309"><strong>待验证：</strong>{"；".join(html.escape(risk) for risk in risks)}</div>' if risk_html else ''}
    """
    return "\n".join(plain_lines), overview_html, detail_html


def _build_analysis_window_digest(summary: dict) -> tuple[str, str]:
    window = summary.get("analysis_window") or {}
    previous = window.get("previous_session") or {}
    morning = window.get("morning_session") or {}
    previous_text = (
        f"前一交易日（{previous.get('date', '日期待核验')}）沪深300 "
        f"{_number(previous.get('change_pct'), 2, '%')}，"
        f"开{_number(previous.get('open'), 2)} / 高{_number(previous.get('high'), 2)} / "
        f"低{_number(previous.get('low'), 2)} / 收{_number(previous.get('close'), 2)}"
    )
    morning_text = (
        f"当日上午：{morning.get('status') or '时间状态待核验'}，"
        f"快照截至{morning.get('as_of') or window.get('generated_at') or '时间待核验'}"
    )
    execution_text = f"执行参考：{window.get('execution_window') or '13:00-14:00'}"
    plain = "\n".join(("午间分析口径", previous_text, morning_text, execution_text))
    section = f"""
      <div style="margin:12px 0;padding:12px 14px;background:#eff6ff;border:1px solid #93c5fd;border-radius:8px">
        <div style="font-size:11px;color:#1d4ed8;font-weight:800;letter-spacing:.5px">NOON ANALYSIS WINDOW</div>
        <div style="font-size:15px;font-weight:800;margin-top:4px">前一交易日复盘 + 当日上午确认</div>
        <div style="font-size:12px;color:#334155;line-height:1.7;margin-top:5px">
          {html.escape(previous_text)}<br>{html.escape(morning_text)}<br>
          <strong>{html.escape(execution_text)}</strong>；下午开盘后仍须核验价格、量能与板块强度。
        </div>
      </div>
    """
    return plain, section


def _build_hot_core_digest(summary: dict) -> tuple[str, str]:
    candidates = summary.get("hot_core_candidates") or []
    if not candidates:
        return "热门核心观察池：本次未形成满足条件的板块龙头/次龙头。", ""
    plain_rows = ["热门核心观察池（强势板块龙头/次龙头）"]
    html_rows = []
    for item in candidates:
        decision = _decision(item)
        decision_status = str(decision.get("status") or "等待确认")
        decision_reason = "；".join(decision.get("reasons") or [])
        plan = item.get("opening_plan") or {}
        entry = plan.get("entry_zone") or {}
        stop = plan.get("stop_zone") or {}
        targets = plan.get("take_profit_zones") or []
        first_target = targets[0] if targets else {}
        news = item.get("related_news") or []
        news_text = "；".join(str(row.get("title") or "") for row in news) or "暂无直接命中个股名称的快讯"
        if plan.get("levels_available") or plan.get("actionable"):
            plan_text = (
                f"进场{_number(entry.get('low'), 2)}-{_number(entry.get('high'), 2)}，"
                f"止损{_number(stop.get('low'), 2)}-{_number(stop.get('high'), 2)}，"
                f"首档止盈{_number(first_target.get('low'), 2)}"
            )
        else:
            plan_text = f"{plan.get('status') or '等待上午盘数据'}：{plan.get('reason') or '尚未形成执行区间'}"
        plain_rows.append(
            f"{item.get('hot_core_rank')}. {item.get('name')}({item.get('code')}) "
            f"{item.get('hot_board')}{item.get('leadership_role')}，{decision_status}；"
            f"{_gate_text(decision)}；{decision_reason}；{plan_text}；新闻：{news_text}"
        )
        news_links = []
        for row in news:
            title = html.escape(str(row.get("title") or "相关新闻"))
            url = html.escape(str(row.get("url") or ""), quote=True)
            news_links.append(
                f'<a href="{url}" style="color:#2563eb;text-decoration:none">{title}</a>'
                if url else title
            )
        news_html = "；".join(news_links) or "暂无直接命中个股名称的快讯"
        html_rows.append(
            '<tr style="border-top:1px solid #e7ebf3"><td style="padding:10px 8px">'
            f'<strong>{html.escape(str(item.get("name") or ""))}</strong>'
            f'<div style="font-size:10px;color:#64748b">{html.escape(str(item.get("code") or ""))} · '
            f'{_number(item.get("price"), 2)}元</div></td>'
            f'<td style="padding:10px 8px"><strong>{html.escape(str(item.get("hot_board") or ""))} · '
            f'{html.escape(str(item.get("leadership_role") or ""))}</strong>'
            f'<div style="font-size:10px;color:#64748b">龙头{_number(item.get("leadership_score"), 0)} · '
            f'轮动{_number(item.get("board_rotation_score"), 0)}</div></td>'
            f'<td style="padding:10px 8px;text-align:center"><strong style="font-size:14px">'
            f'{html.escape(decision_status)}</strong>'
            f'<div style="font-size:10px;color:#64748b">{html.escape(_gate_text(decision))}</div></td>'
            f'<td style="padding:10px 8px;font-size:11px"><strong>{html.escape(plan_text)}</strong>'
            f'<div style="margin-top:3px;color:#c2410c">门槛：{html.escape(decision_reason)}</div>'
            f'<div style="margin-top:3px;color:#64748b">新闻：{news_html}</div>'
            f'<div style="margin-top:3px;color:#b45309">风险：{html.escape(str(item.get("risk") or "请核验公告"))}</div></td></tr>'
        )
    rule = html.escape(str(summary.get("hot_core_rule") or ""))
    section = f"""
      <div style="margin-top:24px;padding:14px;background:#fff7ed;border:1px solid #fed7aa;border-radius:8px 8px 0 0">
        <div style="font-size:10px;letter-spacing:1px;color:#c2410c;font-weight:700">HOT SECTOR LEADERS</div>
        <div style="font-size:20px;font-weight:800;margin-top:3px">热门核心观察池：板块龙头与次龙头</div>
        <div style="font-size:11px;color:#9a3412;margin-top:4px">排名不代表买点；三道门槛全部通过后才进入可执行观察。{rule}</div>
      </div>
      <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="border:1px solid #fed7aa;border-top:0;border-radius:0 0 8px 8px;border-collapse:separate;font-size:12px">
        <thead><tr style="background:#fffaf5;color:#9a3412;font-size:10px">
          <th style="padding:7px;text-align:left">标的</th><th style="padding:7px;text-align:left">所属主线</th>
          <th style="padding:7px">三道门槛</th><th style="padding:7px;text-align:left">执行与新闻</th>
        </tr></thead><tbody>{''.join(html_rows)}</tbody>
      </table>
    """
    return "\n".join(plain_rows), section


def build_email(candidates: list, summary: dict, generated_at: datetime) -> EmailMessage:
    date_text = generated_at.astimezone(SHANGHAI).strftime("%Y-%m-%d")
    rows = []
    ranking_rows = []
    plain_rows = []
    for rank, item in enumerate(candidates, start=1):
        decision = _decision(item)
        decision_status = str(decision.get("status") or "等待确认")
        decision_reason = "；".join(decision.get("reasons") or [])
        matched_themes = item.get("matched_themes") or []
        themes = "、".join(matched_themes) or "--"
        theme_names = "、".join(
            str(theme).split("(", 1)[0].strip()
            for theme in matched_themes[:2]
            if str(theme).strip()
        ) or "--"
        risk = item.get("risk") or "请核验最新公告"
        plan = item.get("opening_plan") or {}
        entry = plan.get("entry_zone") or {}
        stop = plan.get("stop_zone") or {}
        targets = plan.get("take_profit_zones") or []
        first_target = targets[0] if len(targets) > 0 else {}
        second_target = targets[1] if len(targets) > 1 else {}
        if plan.get("levels_available") or plan.get("actionable"):
            plan_text = (
                f"进场{_number(entry.get('low'), 2)}-{_number(entry.get('high'), 2)}，"
                f"突破确认{_number(plan.get('breakout_trigger'), 2)}，"
                f"止损{_number(stop.get('low'), 2)}-{_number(stop.get('high'), 2)}，"
                f"止盈一{_number(first_target.get('low'), 2)}-{_number(first_target.get('high'), 2)}，"
                f"止盈二{_number(second_target.get('low'), 2)}-{_number(second_target.get('high'), 2)}；"
                f"{plan.get('execution_state') or plan.get('status', '')}"
            )
            plan_short = (
                f"进 {_number(entry.get('low'), 2)}–{_number(entry.get('high'), 2)} ｜ "
                f"止 {_number(stop.get('low'), 2)}–{_number(stop.get('high'), 2)} ｜ "
                f"盈 {_number(first_target.get('low'), 2)} / {_number(second_target.get('low'), 2)}"
            )
        else:
            plan_text = f"{plan.get('status') or '等待上午盘'}：{plan.get('reason') or '上午盘计划未形成'}"
            plan_short = f"{plan.get('status') or '等待上午盘'} ｜ {plan.get('reason') or '上午盘计划未形成'}"
        plain_rows.append(
            f"{rank}. {item.get('name', '')}({item.get('code', '')}) "
            f"{decision_status}；{_gate_text(decision)}；"
            f"{decision_reason}；现价{_number(item.get('price'), 2)}；{plan_text}；{risk}"
        )
        ranking_rows.append(
            '<tr style="border-top:1px solid #edf0f5">'
            f'<td style="padding:8px 6px;color:#64748b;text-align:center">{rank}</td>'
            f'<td style="padding:8px 6px"><strong>{html.escape(str(item.get("name", "")))}</strong>'
            f'<div style="font-size:10px;color:#64748b">{html.escape(str(item.get("code", "")))} · {_number(item.get("price"), 2)}元</div></td>'
            f'<td style="padding:8px 6px;text-align:center"><strong style="font-size:13px">{html.escape(decision_status)}</strong>'
            f'<div style="font-size:9px;color:#64748b">{html.escape(_gate_text(decision))}</div></td>'
            f'<td style="padding:8px 6px;font-size:11px;color:#2563eb">{html.escape(theme_names)}</td>'
            f'<td style="padding:8px 6px;font-size:11px">{html.escape(decision_reason or "等待确认")}</td>'
            '</tr>'
        )
        rows.append(
            '<tr><td style="padding:9px 11px;border-top:1px solid #e7ebf3">'
            '<table role="presentation" width="100%" cellspacing="0" cellpadding="0"><tr>'
            f'<td style="vertical-align:top"><span style="display:inline-block;width:22px;height:22px;line-height:22px;'
            f'text-align:center;background:#e8efff;color:#2563eb;border-radius:11px;font-weight:700;font-size:11px">{rank}</span> '
            f'<strong style="font-size:14px">{html.escape(str(item.get("name", "")))}</strong> '
            f'<span style="font-size:11px;color:#64748b">{html.escape(str(item.get("code", "")))} · '
            f'{_number(item.get("price"), 2)}元</span></td>'
            f'<td style="text-align:right;vertical-align:top"><strong style="font-size:15px">{html.escape(decision_status)}</strong>'
            f'<div style="font-size:10px;color:#64748b">{html.escape(_gate_text(decision))}</div></td>'
            '</tr></table>'
            f'<div style="margin-top:5px;font-size:12px"><span style="color:#2563eb">{html.escape(theme_names)}</span> · '
            f'<strong>{html.escape(plan_short)}</strong></div>'
            f'<div style="margin-top:3px;font-size:10px;color:#475569">执行状态：'
            f'{html.escape(str(plan.get("execution_state") or plan.get("status") or "等待确认"))} · '
            f'参考价 {_number(plan.get("reference_price"), 2)} · 禁追 {_number(plan.get("max_chase_price"), 2)}</div>'
            f'<div style="margin-top:3px;font-size:10px;color:#c2410c">决策门槛：{html.escape(decision_reason or "等待确认")}'
            f' · 触发后仓位上限 {_number(decision.get("position_cap"), 0)}元 · 单笔计划亏损上限 {_number(decision.get("allowed_loss"), 0)}元</div>'
            f'<div style="margin-top:3px;font-size:10px;color:#64748b">资金主题：{html.escape(themes)}</div>'
            f'<div style="margin-top:2px;font-size:11px;color:#b45309">风险：{html.escape(str(risk))}</div>'
            '</td></tr>'
        )

    if not rows:
        rows.append('<tr><td colspan="5" style="padding:14px">本次数据源未返回有效候选，请登录网站复核。</td></tr>')
        ranking_rows.append('<tr><td colspan="5" style="padding:14px">本次未返回有效候选。</td></tr>')
        plain_rows.append("本次数据源未返回有效候选，请登录网站复核。")

    scope = html.escape(str(summary.get("scan_scope", "自动任务")))
    generated_text = generated_at.astimezone(SHANGHAI).strftime("%Y-%m-%d %H:%M:%S")
    site_url = os.getenv("SITE_URL", "https://a-share-trading.onrender.com")
    rotation_plain, rotation_overview_html, rotation_detail_html = _build_rotation_digest(summary)
    window_plain, window_html = _build_analysis_window_digest(summary)
    hot_plain, hot_html = _build_hot_core_digest(summary)
    discipline_plain, discipline_html = _build_discipline_digest(summary, candidates)
    body_html = f"""
    <html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
    <body style="margin:0;padding:0;background:#f3f6fb;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','Microsoft YaHei',Arial,sans-serif;color:#172033">
      <table role="presentation" width="100%" cellspacing="0" cellpadding="0"><tr><td align="center" style="padding:16px 8px">
        <table role="presentation" width="720" cellspacing="0" cellpadding="0" style="width:100%;max-width:720px;background:#ffffff;border-radius:12px;box-shadow:0 4px 18px rgba(23,32,51,.08)">
          <tr><td style="padding:20px 22px 14px">
            <div style="font-size:11px;letter-spacing:1px;color:#2563eb;font-weight:700">A-SHARE DAILY BRIEFING</div>
            <div style="font-size:27px;font-weight:850;line-height:1.25;margin-top:5px">{date_text} A股午间分层观察</div>
            <div style="font-size:13px;color:#475569;margin-top:7px;line-height:1.6">顺序固定为市场环境 → 板块强度 → 个股质量 → 实时买点；排名只形成观察池。<br>{scope} · 基本面观察 {len(candidates)} 只 · 热门核心观察 {len(summary.get('hot_core_candidates') or [])} 只 · {generated_text}</div>
            {window_html}
            <div style="margin-top:18px;margin-bottom:8px;font-size:16px;font-weight:800">今日市场速览</div>
            {rotation_overview_html}
            {discipline_html}
            <div style="margin-top:18px;padding:12px 14px;background:#172033;color:#fff;border-radius:8px 8px 0 0">
              <div style="font-size:10px;letter-spacing:1px;color:#93c5fd">WATCHLIST GATES</div>
              <div style="font-size:18px;font-weight:800;margin-top:2px">今日{len(candidates)}只基本面观察池</div>
              <div style="font-size:11px;color:#cbd5e1;margin-top:3px">板块、个股、入场三道门槛全部通过，才进入可执行观察</div>
            </div>
            <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="border:1px solid #dce2ec;border-top:0;border-radius:0 0 8px 8px;border-collapse:separate;font-size:12px">
              <thead><tr style="background:#f8fafc;color:#64748b;font-size:10px">
                <th style="padding:7px 5px">#</th><th style="padding:7px 5px;text-align:left">标的</th>
                <th style="padding:7px 5px">结论 / 三门槛</th><th style="padding:7px 5px;text-align:left">主线</th>
                <th style="padding:7px 5px;text-align:left">原因</th>
              </tr></thead>
              <tbody>{''.join(ranking_rows)}</tbody>
            </table>
            {hot_html}
            {rotation_detail_html}
            <div style="margin-top:24px;padding-top:16px;border-top:2px solid #172033">
              <div style="font-size:11px;letter-spacing:1px;color:#2563eb;font-weight:700">STOCK ANALYSIS</div>
              <div style="margin-top:3px;font-size:20px;font-weight:800">{len(candidates)}只基本面观察详细分析</div>
              <div style="font-size:12px;color:#64748b;margin:4px 0 10px">完整资金主题、基本/技术分、进场区间、突破价、两档止盈、止损与风险</div>
            </div>
            <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="border:1px solid #e7ebf3;border-radius:8px;border-collapse:separate;font-size:13px">
              <tbody>{''.join(rows)}</tbody>
            </table>
            <div style="text-align:center;margin:18px 0 10px">
              <a href="{html.escape(site_url)}" style="display:inline-block;padding:10px 18px;background:#2563eb;color:#fff;text-decoration:none;border-radius:7px;font-size:13px;font-weight:700">查看完整榜单与详细数据</a>
            </div>
            <div style="border-top:1px solid #edf0f5;padding-top:10px;color:#64748b;font-size:11px;line-height:1.6">
              仅供量化研究，不构成投资建议。行情、财务和资金数据可能延迟，交易前请核验公告并独立决策。
            </div>
          </td></tr>
        </table>
      </td></tr></table>
    </body></html>
    """

    message = EmailMessage()
    message["Subject"] = f"{date_text} A股午间分层观察（昨盘+上午盘）"
    message.set_content(
        f"{date_text} A股午间分层观察\n排名只形成观察池，不代表买入或下午上涨预测。\n\n"
        + window_plain
        + "\n\n" + rotation_plain
        + "\n\n" + discipline_plain
        + "\n\n中长期观察池\n"
        + "\n".join(plain_rows)
        + "\n\n" + hot_plain
        + f"\n\n完整页面：{site_url}\n\n本邮件仅供研究，不构成投资建议。"
    )
    message.add_alternative(body_html, subtype="html")
    return message


def _recipients() -> list[str]:
    return [
        address.strip()
        for address in os.environ["MAIL_TO"].replace(";", ",").split(",")
        if address.strip()
    ]


def _send_email_via_brevo(message: EmailMessage, api_key: str) -> None:
    username = os.environ["MAIL_USERNAME"]
    recipients = _recipients()
    if not recipients:
        raise ValueError("MAIL_TO 未配置有效的收件邮箱")
    sender = os.getenv("BREVO_SENDER_EMAIL", os.getenv("MAIL_FROM", username))
    plain_part = message.get_body(preferencelist=("plain",))
    html_part = message.get_body(preferencelist=("html",))
    payload = {
        "sender": {
            "name": os.getenv("MAIL_FROM_NAME", "A股日报"),
            "email": sender,
        },
        "to": [{"email": address} for address in recipients],
        "subject": str(message["Subject"]),
        "textContent": plain_part.get_content() if plain_part else "",
        "htmlContent": html_part.get_content() if html_part else "",
    }
    request = Request(
        "https://api.brevo.com/v3/smtp/email",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "accept": "application/json",
            "api-key": api_key,
            "content-type": "application/json",
        },
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
    host = os.getenv("MAIL_SMTP_HOST", "smtp.qq.com")
    port = int(os.getenv("MAIL_SMTP_PORT", "465"))
    username = os.environ["MAIL_USERNAME"]
    password = os.environ["MAIL_PASSWORD"]
    recipients = _recipients()
    if not recipients:
        raise ValueError("MAIL_TO 未配置有效的收件邮箱")
    sender = os.getenv("MAIL_FROM", username)

    message["From"] = sender
    message["To"] = ", ".join(recipients)
    if port == 465:
        with smtplib.SMTP_SSL(host, port, timeout=30) as smtp:
            smtp.login(username, password)
            smtp.send_message(message)
    else:
        with smtplib.SMTP(host, port, timeout=30) as smtp:
            smtp.starttls()
            smtp.login(username, password)
            smtp.send_message(message)


def send_email(message: EmailMessage) -> None:
    api_key = os.getenv("BREVO_API_KEY", "").strip()
    if api_key:
        _send_email_via_brevo(message, api_key)
    else:
        _send_email_via_smtp(message)


def main() -> None:
    universe_limit = int(os.getenv("EMAIL_UNIVERSE_LIMIT", "500"))
    count = int(os.getenv("EMAIL_STOCK_COUNT", "10"))
    candidates, summary = select_candidates(universe_limit, count)
    message = build_email(candidates, summary, datetime.now(SHANGHAI))
    send_email(message)
    print(f"已发送选股日报：{len(candidates)} 只候选。")


if __name__ == "__main__":
    main()
