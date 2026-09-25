"""Small, read-only source probe; never loads accounts or generates/sends email."""
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from data_feed import DataFeed, SHANGHAI
from noon_holdings import build_noon_action
from trading_calendar import market_closed_reason


def main():
    feed = DataFeed()
    mapping = feed.get_stock_industry_indices(["600183", "600479"])

    def probe(spec):
        code, host = spec
        params = {"secid": f"{'90' if code.startswith('BK') else '1'}.{code}",
                  "fields1": "f1,f2,f3,f4,f5,f6,f7,f8",
                  "fields2": "f51,f52,f53,f54,f55,f56,f57,f58", "ndays": 1, "iscr": 0}
        response = feed._request(f"https://{host}/api/qt/stock/trends2/get", params,
                                 timeout=(3, 8), retries=1)
        result = {"index": code, "host": host, "response": response is not None}
        if response is not None:
            try:
                data = response.json().get("data") or {}
                rows = data.get("trends") or []
                result.update(name=data.get("name"), count=len(rows),
                              first=rows[0] if rows else None, last=rows[-1] if rows else None)
                if rows:
                    source_date = rows[0].split(",")[0][:10].replace("-", "")
                    result["replay_quote"] = feed._parse_index_morning(rows, source_date)
            except (ValueError, AttributeError, TypeError):
                result["invalid_json"] = True
        return result

    specs = [(code, host) for code in ("000300", "BK0459", "BK1040")
             for host in ("push2his.eastmoney.com", "push2delay.eastmoney.com")]
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(probe, specs))
    quotes = {r["index"]: r["replay_quote"] for r in results
              if (r.get("replay_quote") or {}).get("available")}
    replay = []
    for code, industry in mapping.items():
        stock = feed.get_intraday_minute(code)
        market = quotes.get("000300", {})
        sector = quotes.get(industry["index_code"], {})
        date = market.get("trade_date")
        if not date:
            continue
        # Historical source-chain test only: no real account, sizing or trade advice.
        action = build_noon_action(
            {"code": code, "quantity": 0, "available_quantity": 0, "cost_price": 0},
            {"available": True, "qualified": True, "close": 1, "stop_price": 0.01},
            {}, stock, market, sector, datetime.strptime(date, "%Y%m%d").replace(hour=12),
            industry["name"])
        replay.append({"stock": code, "source_date": date, "ready": action["morning_ready"],
                       "contexts": {key: value["valid"] for key, value in action["morning_evidence"].items()}})
    evidence = {"checked_at": datetime.now(SHANGHAI).isoformat(),
                "market_closed_reason": market_closed_reason(datetime.now(SHANGHAI).date()),
                "company_index_mapping": mapping, "indices": results,
                "historical_replay_only": True, "replay": replay}
    path = Path("output/verification/noon-sources.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(evidence, ensure_ascii=False))
    if len(replay) != 2 or not all(row["ready"] for row in replay):
        raise RuntimeError("历史行情链路回放未通过；未生成或发送邮件")


if __name__ == "__main__":
    main()
