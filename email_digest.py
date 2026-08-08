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
from fundamental import LongTermFundamentalScreener


SHANGHAI = ZoneInfo("Asia/Shanghai")


def _number(value, digits=1, suffix="") -> str:
    try:
        return f"{float(value):.{digits}f}{suffix}"
    except (TypeError, ValueError):
        return "--"


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


def _build_rotation_digest(summary: dict) -> tuple[str, str]:
    analysis = summary.get("rotation_analysis") or {}
    boards = (summary.get("rotation_boards") or [])[:3]
    ai_used = bool(analysis.get("available"))
    mode = "AI辅助轮动" if ai_used else "资金规则轮动"
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
            f"{state}/{confidence}"
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
            '<div style="font-size:12px;color:#64748b">轮动分</div></td>'
            f'<td style="padding:10px 8px"><strong>{html.escape(str(state))} / {html.escape(str(confidence))}</strong>'
            f'<div style="font-size:12px;color:#64748b">触发：{html.escape(str(trigger))}；'
            f'失效：{html.escape(str(invalidation))}</div></td>'
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
    html_section = f"""
      <div style="margin-top:18px;margin-bottom:8px;font-size:16px;font-weight:700;color:#172033">资金与板块轮动</div>
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
    return "\n".join(plain_lines), html_section


def build_email(candidates: list, summary: dict, generated_at: datetime) -> EmailMessage:
    date_text = generated_at.astimezone(SHANGHAI).strftime("%Y-%m-%d")
    rows = []
    plain_rows = []
    compact_candidates = []
    for rank, item in enumerate(candidates, start=1):
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
        if plan.get("actionable"):
            plan_text = (
                f"进场{_number(entry.get('low'), 2)}-{_number(entry.get('high'), 2)}，"
                f"突破确认{_number(plan.get('breakout_trigger'), 2)}，"
                f"止损{_number(stop.get('low'), 2)}-{_number(stop.get('high'), 2)}"
            )
            plan_short = (
                f"进 {_number(entry.get('low'), 2)}–{_number(entry.get('high'), 2)} ｜ "
                f"破 {_number(plan.get('breakout_trigger'), 2)} ｜ "
                f"止 {_number(stop.get('low'), 2)}–{_number(stop.get('high'), 2)}"
            )
        else:
            plan_text = f"{plan.get('status') or '等待10:00'}：{plan.get('reason') or '首30分钟计划未形成'}"
            plan_short = f"{plan.get('status') or '等待10:00'} ｜ {plan.get('reason') or '首30分钟计划未形成'}"
        plain_rows.append(
            f"{rank}. {item.get('name', '')}({item.get('code', '')}) "
            f"综合{_number(item.get('selection_score'), 0)}分，"
            f"现价{_number(item.get('price'), 2)}；{plan_text}；{risk}"
        )
        if rank <= 5:
            rows.append(
                '<tr><td style="padding:10px 11px;border-top:1px solid #e7ebf3">'
                '<table role="presentation" width="100%" cellspacing="0" cellpadding="0"><tr>'
                f'<td style="vertical-align:top"><span style="display:inline-block;width:22px;height:22px;line-height:22px;'
                f'text-align:center;background:#e8efff;color:#2563eb;border-radius:11px;font-weight:700;font-size:11px">{rank}</span> '
                f'<strong style="font-size:14px">{html.escape(str(item.get("name", "")))}</strong> '
                f'<span style="font-size:11px;color:#64748b">{html.escape(str(item.get("code", "")))} · '
                f'{_number(item.get("price"), 2)}元</span></td>'
                f'<td style="text-align:right;vertical-align:top"><strong style="font-size:18px">{_number(item.get("selection_score"), 0)}</strong>'
                f'<span style="font-size:10px;color:#64748b"> 分</span><div style="font-size:10px;color:#64748b">'
                f'基{_number(item.get("fundamental_score"), 0)} · 技{_number(item.get("technical_score"), 0)}</div></td>'
                '</tr></table>'
                f'<div style="margin-top:6px;font-size:12px"><span title="{html.escape(themes)}" style="color:#2563eb">{html.escape(theme_names)}</span> · '
                f'<strong>{html.escape(plan_short)}</strong></div>'
                f'<div style="margin-top:3px;font-size:11px;color:#b45309">风险：{html.escape(str(risk))}</div>'
                '</td></tr>'
            )
        else:
            compact_candidates.append(
                f'<span style="display:inline-block;margin:3px 6px 3px 0;padding:6px 9px;'
                f'background:#f8fafc;border:1px solid #e7ebf3;border-radius:14px;font-size:12px">'
                f'{rank}. {html.escape(str(item.get("name", "")))} '
                f'<strong>{_number(item.get("selection_score"), 0)}分</strong></span>'
            )

    if not rows:
        rows.append('<tr><td colspan="5" style="padding:14px">本次数据源未返回有效候选，请登录网站复核。</td></tr>')
        plain_rows.append("本次数据源未返回有效候选，请登录网站复核。")

    scope = html.escape(str(summary.get("scan_scope", "自动任务")))
    generated_text = generated_at.astimezone(SHANGHAI).strftime("%Y-%m-%d %H:%M:%S")
    site_url = os.getenv("SITE_URL", "https://a-share-trading.onrender.com")
    rotation_plain, rotation_html = _build_rotation_digest(summary)
    body_html = f"""
    <html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
    <body style="margin:0;padding:0;background:#f3f6fb;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','Microsoft YaHei',Arial,sans-serif;color:#172033">
      <table role="presentation" width="100%" cellspacing="0" cellpadding="0"><tr><td align="center" style="padding:16px 8px">
        <table role="presentation" width="720" cellspacing="0" cellpadding="0" style="width:100%;max-width:720px;background:#ffffff;border-radius:12px;box-shadow:0 4px 18px rgba(23,32,51,.08)">
          <tr><td style="padding:20px 22px 14px">
            <div style="font-size:12px;color:#64748b">A股中长期研究 · {generated_text}</div>
            <div style="font-size:24px;font-weight:800;margin-top:4px">{date_text} 选股日报</div>
            <div style="font-size:12px;color:#64748b;margin-top:5px">{scope} · 精选 {len(candidates)} 只</div>
            {rotation_html}
            <div style="margin-top:18px;margin-bottom:8px;font-size:16px;font-weight:700">重点观察前5只</div>
            <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="border:1px solid #e7ebf3;border-radius:8px;border-collapse:separate;font-size:13px">
              <tbody>{''.join(rows)}</tbody>
            </table>
            {f'<div style="margin-top:10px"><strong style="font-size:12px">其余候选：</strong>{"".join(compact_candidates)}</div>' if compact_candidates else ''}
            <div style="text-align:center;margin:18px 0 10px">
              <a href="{html.escape(site_url)}" style="display:inline-block;padding:10px 18px;background:#2563eb;color:#fff;text-decoration:none;border-radius:7px;font-size:13px;font-weight:700">查看完整10只与详细数据</a>
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
    message["Subject"] = f"{date_text} A股中长期选股日报"
    message.set_content(
        f"{date_text} A股中长期选股日报\n\n"
        + rotation_plain
        + "\n\n中长期精选股票\n"
        + "\n".join(plain_rows)
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
