"""Generate and email the daily medium/long-term A-share shortlist."""

from __future__ import annotations

import html
import os
import smtplib
from datetime import datetime
from email.message import EmailMessage
from zoneinfo import ZoneInfo

from data_feed import DataFeed
from fundamental import LongTermFundamentalScreener


SHANGHAI = ZoneInfo("Asia/Shanghai")


def _number(value, digits=1, suffix="") -> str:
    try:
        return f"{float(value):.{digits}f}{suffix}"
    except (TypeError, ValueError):
        return "--"


def select_candidates(universe_limit: int, count: int) -> tuple[list, dict]:
    screener = LongTermFundamentalScreener(data_feed=DataFeed())
    ranked = screener.screen(universe_limit=universe_limit)
    recommended = [item for item in ranked if item.get("recommendation_rank")]
    recommended.sort(key=lambda item: item["recommendation_rank"])
    if not recommended:
        recommended = sorted(
            ranked,
            key=lambda item: item.get("selection_score", 0),
            reverse=True,
        )
    return recommended[:count], screener.summary


def build_email(candidates: list, summary: dict, generated_at: datetime) -> EmailMessage:
    date_text = generated_at.astimezone(SHANGHAI).strftime("%Y-%m-%d")
    rows = []
    plain_rows = []
    for rank, item in enumerate(candidates, start=1):
        themes = "、".join(item.get("matched_themes") or []) or "--"
        risk = item.get("risk") or "请核验最新公告"
        plain_rows.append(
            f"{rank}. {item.get('name', '')}({item.get('code', '')}) "
            f"综合{_number(item.get('selection_score'), 0)}分，"
            f"现价{_number(item.get('price'), 2)}；{risk}"
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
            f"<td>{html.escape(str(risk))}</td>"
            "</tr>"
        )

    if not rows:
        rows.append('<tr><td colspan="7">本次数据源未返回有效候选，请登录网站复核。</td></tr>')
        plain_rows.append("本次数据源未返回有效候选，请登录网站复核。")

    scope = html.escape(str(summary.get("scan_scope", "自动任务")))
    generated_text = generated_at.astimezone(SHANGHAI).strftime("%Y-%m-%d %H:%M:%S")
    site_url = os.getenv("SITE_URL", "https://a-share-trading.onrender.com")
    body_html = f"""
    <html><body style="font-family:Arial,'Microsoft YaHei',sans-serif;color:#172033">
      <h2>{date_text} A股中长期选股日报</h2>
      <p>扫描范围：{scope}；生成时间：{generated_text}（北京时间）</p>
      <table style="border-collapse:collapse;width:100%;font-size:14px" border="1" cellpadding="8">
        <thead style="background:#eef3ff"><tr>
          <th>排名</th><th>标的</th><th>现价</th><th>精选分</th>
          <th>基本/技术</th><th>资金主题</th><th>风险提示</th>
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
        + "\n".join(plain_rows)
        + f"\n\n完整页面：{site_url}\n\n本邮件仅供研究，不构成投资建议。"
    )
    message.add_alternative(body_html, subtype="html")
    return message


def send_email(message: EmailMessage) -> None:
    host = os.getenv("MAIL_SMTP_HOST", "smtp.qq.com")
    port = int(os.getenv("MAIL_SMTP_PORT", "465"))
    username = os.environ["MAIL_USERNAME"]
    password = os.environ["MAIL_PASSWORD"]
    recipients = [
        address.strip()
        for address in os.environ["MAIL_TO"].replace(";", ",").split(",")
        if address.strip()
    ]
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


def main() -> None:
    universe_limit = int(os.getenv("EMAIL_UNIVERSE_LIMIT", "500"))
    count = int(os.getenv("EMAIL_STOCK_COUNT", "10"))
    candidates, summary = select_candidates(universe_limit, count)
    message = build_email(candidates, summary, datetime.now(SHANGHAI))
    send_email(message)
    print(f"已发送选股日报：{len(candidates)} 只候选。")


if __name__ == "__main__":
    main()
