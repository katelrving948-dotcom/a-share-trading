"""Small, read-only source probe; never loads accounts or generates/sends email."""
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from data_feed import DataFeed, SHANGHAI


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
            except (ValueError, AttributeError, TypeError):
                result["invalid_json"] = True
        return result

    specs = [(code, host) for code in ("000300", "BK0459", "BK1040")
             for host in ("push2his.eastmoney.com", "push2delay.eastmoney.com")]
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(probe, specs))
    evidence = {"checked_at": datetime.now(SHANGHAI).isoformat(),
                "company_index_mapping": mapping, "indices": results}
    path = Path("output/verification/noon-sources.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(evidence, ensure_ascii=False))


if __name__ == "__main__":
    main()
