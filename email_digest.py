"""Generate and send the noon report from fundamental and technical scores."""

from __future__ import annotations

import html
import json
import os
import smtplib
from email.message import EmailMessage
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from research_core import build_push_payload


def _number(value, digits=1, suffix="") -> str:
    try:
        return f"{float(value):.{digits}f}{suffix}"
    except (TypeError, ValueError):
        return "--"


def build_email(payload: dict) -> EmailMessage:
    observations = payload.get("observations", [])
    plain_rows = []
    html_rows = []
    for item in observations:
        plain_rows.append(
            f"{item.get('rank', '-')}. {item.get('code')} {item.get('name', '')} | "
            f"基本面{_number(item.get('fundamental_score'), 0)} | "
            f"技术面{_number(item.get('technical_score'), 1)} | "
            f"综合{_number(item.get('combined_score'), 1)}"
        )
        html_rows.append(
            "<tr>"
            f"<td>{item.get('rank', '-')}</td>"
            f"<td><strong>{html.escape(str(item.get('code', '')))}</strong><br>{html.escape(str(item.get('name', '')))}</td>"
            f"<td>{html.escape(str(item.get('industry', '') or '--'))}</td>"
            f"<td>{_number(item.get('fundamental_score'), 0)}</td>"
            f"<td>{_number(item.get('technical_score'), 1)}</td>"
            f"<td><strong>{_number(item.get('combined_score'), 1)}</strong></td>"
            "</tr>"
        )

    rules = payload.get("rules", {})
    technical = payload.get("technical_summary", {})
    metadata = technical.get("metadata", {})
    metrics = technical.get("oos_metrics", {})
    market = payload.get("market", {})
    breadth = market.get("breadth") or market.get("stats") or market
    empty_text = "今日没有股票同时达到两类评分阈值，保留空观察池。"
    site_url = os.getenv("SITE_URL", "https://a-share-trading.onrender.com")
    plain = (
        f"{payload.get('subject')}\n"
        f"生成时间：{payload.get('generated_at')}\n"
        f"分析窗口：{payload.get('analysis_window')}\n"
        f"使用时段：{payload.get('execution_window')}\n\n"
        "市场概况\n"
        f"上涨：{breadth.get('up', '--')}；下跌：{breadth.get('down', '--')}；"
        f"涨停：{breadth.get('limit_up', '--')}；跌停：{breadth.get('limit_down', '--')}\n\n"
        "双评分交集观察池\n"
        + ("\n".join(plain_rows) if plain_rows else empty_text)
        + "\n\n"
        f"规则：基本面≥{rules.get('fundamental_min')}，技术面≥{rules.get('technical_min')}；"
        "展示数量只是版面上限，不是固定选十只。\n"
        f"量化信号日期：{metadata.get('signal_date', '--')}；"
        f"样本外年化：{_number(metrics.get('annual_return'), 2, '%')}；"
        f"最大回撤：{_number(metrics.get('max_drawdown'), 2, '%')}；"
        f"夏普：{_number(metrics.get('sharpe_ratio'), 2)}\n\n"
        "本报告只展示基本面和技术面评分，不生成仓位、进场价、止盈止损或自动委托。\n"
        f"网站：{site_url}"
    )
    table_body = "".join(html_rows) if html_rows else f'<tr><td colspan="6">{empty_text}</td></tr>'
    body = f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"></head>
    <body style="margin:0;background:#f3f6fa;font-family:Arial,'Microsoft YaHei',sans-serif;color:#172033">
      <table role="presentation" width="100%"><tr><td align="center" style="padding:24px">
        <table role="presentation" width="760" style="max-width:100%;background:#fff;border-collapse:collapse;border:1px solid #dce3ec">
          <tr><td style="padding:24px;background:#10223f;color:white">
            <div style="font-size:12px;color:#9fc2ff">FUNDAMENTAL × TECHNICAL</div>
            <h1 style="margin:6px 0 8px;font-size:24px">{html.escape(str(payload.get('subject')))}</h1>
            <div style="color:#c8d7ea">{html.escape(str(payload.get('analysis_window')))} · {html.escape(str(payload.get('execution_window')))}</div>
          </td></tr>
          <tr><td style="padding:22px">
            <div style="padding:12px;background:#eef5ff;border-left:4px solid #286fe8;line-height:1.7">
              上涨 {breadth.get('up', '--')} · 下跌 {breadth.get('down', '--')} ·
              涨停 {breadth.get('limit_up', '--')} · 跌停 {breadth.get('limit_down', '--')}
            </div>
            <h2 style="font-size:18px;margin-top:24px">双评分交集观察池</h2>
            <p style="color:#5e6b7d">基本面≥{rules.get('fundamental_min')}，技术面≥{rules.get('technical_min')}。没有交集时允许为空。</p>
            <table width="100%" style="border-collapse:collapse;font-size:13px">
              <thead><tr style="background:#edf2f8"><th>序</th><th>股票</th><th>行业</th><th>基本面</th><th>技术面</th><th>综合</th></tr></thead>
              <tbody>{table_body}</tbody>
            </table>
            <h2 style="font-size:18px;margin-top:24px">量化样本外表现</h2>
            <p>信号日期 {metadata.get('signal_date', '--')} · 年化 {_number(metrics.get('annual_return'), 2, '%')} ·
            最大回撤 {_number(metrics.get('max_drawdown'), 2, '%')} · 夏普 {_number(metrics.get('sharpe_ratio'), 2)}</p>
            <div style="margin-top:22px;padding:12px;background:#fff7ed;border-left:4px solid #f59e0b;color:#7c2d12">
              评分仅用于研究观察，不生成交易计划、仓位、止盈止损或自动委托。回测表现不保证未来收益。
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
    send_email(build_email(payload))
    print(f"已发送双评分午间报告：{payload['observation_count']} 只交集观察标的。")


if __name__ == "__main__":
    main()
