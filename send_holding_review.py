"""Explicitly requested historical holding review; never changes normal send markers."""
import html
import json
import os
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path

from check_noon_sources import main as check_sources
from data_feed import DataFeed, SHANGHAI
from email_digest import send_email
from research_core import build_account_holding_actions, load_account_state


def review_message(rows, date):
    if not rows or not all(row.get("morning_ready") for row in rows):
        raise RuntimeError("持仓回放数据不完整，未发送预览")
    title = f"【修复预览·非今日交易建议】{date} 午间持仓建议"
    notice = f"本邮件仅供检查修复效果，行情截至 {date} 11:30。以下为历史回放结果，不是今天的交易指令。"
    plain = [title, notice]
    table = []
    for row in rows:
        values = [f"{row.get('code', '')} {row.get('name', '')}",
                  f"成本 {row.get('cost_price', '--')}；午间参考价 {row.get('reference_price', '--')}",
                  str(row.get("sector_name") or ""), str(row.get("action") or ""),
                  str(row.get("reason") or "")]
        plain.append(" | ".join(values))
        table.append("<tr>" + "".join(f"<td>{html.escape(value)}</td>" for value in values) + "</tr>")
    message = EmailMessage()
    message["Subject"] = title
    message.set_content("\n\n".join(plain))
    body = f"""<html><body style="font-family:sans-serif;line-height:1.6">
    <h2>{html.escape(title)}</h2><p style="background:#fff4db;padding:16px">{html.escape(notice)}</p>
    <p>个股、沪深300、所属行业的上午行情均已通过日期、完整性及均价校验。</p>
    <table border="1" cellpadding="10" cellspacing="0" style="border-collapse:collapse;width:100%">
    <tr><th>持仓</th><th>成本/行情</th><th>所属行业</th><th>历史参考动作</th><th>判断依据</th></tr>
    {''.join(table)}</table></body></html>"""
    message.add_alternative(body, subtype="html")
    return message


def main():
    # An explicit date is mandatory; never silently reinterpret this as today's mail.
    date = os.environ["REVIEW_DATE"]
    replay_now = datetime.strptime(date, "%Y-%m-%d").replace(hour=12, tzinfo=SHANGHAI)
    if replay_now.date() >= datetime.now(SHANGHAI).date():
        raise ValueError("此入口仅支持明确标注的历史回放")
    check_sources()
    evidence = json.loads(Path("output/verification/noon-sources.json").read_text(encoding="utf-8"))
    quotes = {row["index"]: row["replay_quote"] for row in evidence["indices"]
              if (row.get("replay_quote") or {}).get("available")
              and row["replay_quote"].get("trade_date") == date.replace("-", "")}

    class ReviewFeed(DataFeed):
        def get_index_morning(self, code):
            return quotes.get(code, {"available": False})

    account = load_account_state(now=datetime.now(SHANGHAI))
    rows = build_account_holding_actions(account, holding_feed=ReviewFeed(), now=replay_now)
    message = review_message(rows, date)
    # Persist only counts; holdings and recipient details remain private.
    quality = {"review_date": date, "holding_count": len(rows),
               "holding_ready_count": sum(bool(row.get("morning_ready")) for row in rows), "sent": False}
    path = Path("output/verification/holding-review-send.json")
    path.write_text(json.dumps(quality), encoding="utf-8")
    send_email(message)
    quality["sent"] = True
    path.write_text(json.dumps(quality), encoding="utf-8")
    print(f"持仓修复预览已发送：{date}；{len(rows)}项行情校验通过")


if __name__ == "__main__":
    main()
