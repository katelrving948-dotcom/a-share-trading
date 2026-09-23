"""Dated Sina industry fallback; never substitute total flow for main flow."""
import math
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd


BASE = "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
SOURCE = "新浪证监会行业资金（主力口径，分类与东方财富不同）"


class SinaIndustryFeed:
    def __init__(self, request):
        self.request = request
        self.date = ""
        self.rows = []
        self.history = {}
        self.members = {}
        self.loaded_at = 0

    def _read(self, method, params):
        response = self.request(BASE + method, params, timeout=(3, 10), retries=1,
                                headers={"Referer": "https://finance.sina.com.cn/"})
        if response is None:
            return None
        try:
            return response.json()
        except (ValueError, TypeError):
            return None

    def load(self):
        today = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d")
        if self.date == today and self.rows and time.monotonic() - self.loaded_at < 120:
            return pd.DataFrame(self.rows)
        self.rows, self.history, self.members = [], {}, {}
        listing = self._read("MoneyFlow.ssl_bkzj_bk",
                             {"page": 1, "num": 200, "sort": "netamount", "asc": 0, "fenlei": 2})
        if not isinstance(listing, list):
            return pd.DataFrame()

        def dated_row(item):
            if not isinstance(item, dict):
                return None
            category = str(item.get("category") or "")
            if not category.startswith("hangye_Z") or not item.get("name"):
                return None
            history = self._read("MoneyFlow.ssl_bkzj_zjlrqs",
                                 {"bankuai": category, "page": 1, "num": 5,
                                  "sort": "opendate", "asc": 0})
            if (not isinstance(history, list) or not history or not isinstance(history[0], dict)
                    or history[0].get("opendate") != today):
                return None
            try:
                latest = history[0]
                flow, ratio, change, price = [float(latest[key]) for key in
                                              ("r0_net", "r0_ratio", "avg_changeratio", "avg_price")]
                if not all(math.isfinite(v) for v in (flow, ratio, change, price)) or price <= 0:
                    return None
            except (KeyError, ValueError, TypeError):
                return None
            code = "sina:" + category
            self.history[code] = history
            return {"code": code, "name": item["name"], "price": price,
                    "change_pct": round(change * 100, 2), "main_net_inflow": round(flow / 1e8, 2),
                    "main_net_pct": round(ratio * 100, 2), "rise_count": None, "fall_count": None,
                    "source": SOURCE, "trade_date": today}

        with ThreadPoolExecutor(max_workers=4) as executor:
            self.rows = [row for row in executor.map(dated_row, listing) if row]
        self.rows.sort(key=lambda row: row["main_net_inflow"], reverse=True)
        self.date = today
        self.loaded_at = time.monotonic()
        print(f"[DataFeed] Sina industry fallback: {len(self.rows)}/{len(listing)} dated boards ({today})")
        return pd.DataFrame(self.rows)

    def constituents(self, code):
        if code in self.members:
            return self.members[code]
        category = code.removeprefix("sina:")
        count = self._read("Market_Center.getHQNodeStockCount", {"node": category})
        try:
            count = int(count)
        except (ValueError, TypeError):
            return set()
        if count <= 0 or count > 10000:
            return set()
        members = set()
        for page in range(1, math.ceil(count / 100) + 1):
            rows = self._read("Market_Center.getHQNodeData",
                              {"node": category, "page": page, "num": 100, "sort": "symbol", "asc": 1})
            if not isinstance(rows, list):
                return set()
            members.update(str(row["code"]) for row in rows
                           if str(row.get("code", "")).isdigit() and len(str(row["code"])) == 6)
        if len(members) < count * 0.95:
            return set()
        self.members[code] = members
        return members

    def industries(self, codes):
        wanted = set(codes)
        result = {}
        def resolve(row):
            return row["name"], self.constituents(row["code"])
        with ThreadPoolExecutor(max_workers=4) as executor:
            for name, members in executor.map(resolve, self.rows):
                for code in wanted.intersection(members):
                    result[code] = name
        return result

    def flow_history(self, code, days):
        rows = self.history.get(code, [])[:days]
        try:
            today = datetime.fromisoformat(self.date)
            rows = [row for row in rows if 0 <= (today - datetime.fromisoformat(row["opendate"])).days <= 14]
            flows = [float(row["r0_net"]) / 1e8 for row in rows]
            if not all(math.isfinite(value) for value in flows):
                flows = []
        except (KeyError, ValueError, TypeError):
            flows = []
        return {"recent_main_net_inflow": round(sum(flows), 2) if flows else None,
                "positive_days": sum(value > 0 for value in flows) if flows else None,
                "days": len(flows)}
