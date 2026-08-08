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
    boards = (summary.get("rotation_boards") or [])[:5]
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
            "<tr>"
            f"<td>{index}</td>"
            f"<td><strong>{html.escape(str(board.get('name', '--')))}</strong><br>"
            f"{html.escape(str(board.get('type', '--')))}</td>"
            f"<td>{_number(board.get('change_pct'), 2, '%')}</td>"
            f"<td>{_number(board.get('main_net_inflow'), 2, '亿')}</td>"
            f"<td>{html.escape(recent_text)}</td>"
            f"<td>{_number(board.get('rotation_score'), 0)}</td>"
            f"<td>{html.escape(str(state))} / {html.escape(str(confidence))}<br>"
            f"触发：{html.escape(str(trigger))}<br>"
            f"失效：{html.escape(str(invalidation))}</td>"
            "</tr>"
        )

    if not board_rows:
        board_rows.append('<tr><td colspan="7">本次未取得有效板块资金数据。</td></tr>')

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
      <h3 style="margin-top:24px">资金流动与板块轮动</h3>
      <div style="background:#f5f8ff;padding:12px 16px;border-radius:8px;margin-bottom:12px">
        <strong>{html.escape(mode)}</strong>；市场阶段：{html.escape(str(market_stage))}<br>
        1–3日：{html.escape(str(short_outlook))}<br>
        3–10日：{html.escape(str(medium_outlook))}
        {f'<br>阶段依据：{html.escape(str(stage_reason))}' if stage_reason else ''}
      </div>
      <table style="border-collapse:collapse;width:100%;font-size:14px" border="1" cellpadding="8">
        <thead style="background:#eef3ff"><tr>
          <th>排名</th><th>板块</th><th>涨跌</th><th>当日主力净流入</th>
          <th>近5日主力净流入</th><th>轮动分</th><th>轮动判断</th>
        </tr></thead>
        <tbody>{''.join(board_rows)}</tbody>
      </table>
      {f'<p><strong>轮动路径</strong></p><ul>{path_html}</ul>' if path_html else ''}
      {f'<p><strong>风险与待验证项</strong></p><ul>{risk_html}</ul>' if risk_html else ''}
    """
    return "\n".join(plain_lines), html_section


def build_email(candidates: list, summary: dict, generated_at: datetime) -> EmailMessage:
    date_text = generated_at.astimezone(SHANGHAI).strftime("%Y-%m-%d")
    rows = []
    plain_rows = []
    for rank, item in enumerate(candidates, start=1):
        themes = "、".join(item.get("matched_themes") or []) or "--"
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
        else:
            plan_text = f"{plan.get('status') or '等待10:00'}：{plan.get('reason') or '首30分钟计划未形成'}"
        plain_rows.append(
            f"{rank}. {item.get('name', '')}({item.get('code', '')}) "
            f"综合{_number(item.get('selection_score'), 0)}分，"
            f"现价{_number(item.get('price'), 2)}；{plan_text}；{risk}"
        )
        rows.append(
            "<tr>"
            f"<td>{rank}</td>"
            f"<td><strong>{html.escape(str(item.get('name', '')))}</strong><br>"
            f"{html.escape(str(item.get('code', '')))}</td>"
            f"<td>{_number(item.get('price'), 2)}</td>"
            f"<td>{_number(item.get('selection_score'), 0)}</td>"
            f"<td>{_number(item.get('fundamental_score'), 0)} / "
            f"{_number(item.get('technical_score'), 0)}</td>"
            f"<td>{html.escape(themes)}</td>"
            f"<td>{html.escape(plan_text)}</td>"
            f"<td>{html.escape(str(risk))}</td>"
            "</tr>"
        )

    if not rows:
        rows.append('<tr><td colspan="8">本次数据源未返回有效候选，请登录网站复核。</td></tr>')
        plain_rows.append("本次数据源未返回有效候选，请登录网站复核。")

    scope = html.escape(str(summary.get("scan_scope", "自动任务")))
    generated_text = generated_at.astimezone(SHANGHAI).strftime("%Y-%m-%d %H:%M:%S")
    site_url = os.getenv("SITE_URL", "https://a-share-trading.onrender.com")
    rotation_plain, rotation_html = _build_rotation_digest(summary)
    body_html = f"""
    <html><body style="font-family:Arial,'Microsoft YaHei',sans-serif;color:#172033">
      <h2>{date_text} A股中长期选股日报</h2>
      <p>扫描范围：{scope}；生成时间：{generated_text}（北京时间）</p>
      {rotation_html}
      <h3 style="margin-top:24px">中长期精选股票</h3>
      <table style="border-collapse:collapse;width:100%;font-size:14px" border="1" cellpadding="8">
        <thead style="background:#eef3ff"><tr>
          <th>排名</th><th>标的</th><th>现价</th><th>精选分</th>
          <th>基本/技术</th><th>资金主题</th><th>10:00进场与止损</th><th>风险提示</th>
        </tr></thead>
        <tbody>{''.join(rows)}</tbody>
      </table>
      <p><a href="{html.escape(site_url)}">打开在线投研终端查看完整数据</a></p>
      <p style="color:#687386;font-size:12px">
        本邮件仅为量化研究结果，不构成投资建议。行情、财务和资金数据可能延迟或缺失，交易前请核验公告并独立决策。
      </p>
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
