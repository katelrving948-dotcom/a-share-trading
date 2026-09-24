"""Dated Sina industry fallback; never substitute total flow for main flow."""
import math
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd


BASE = "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
SOURCE = "新浪证监会行业11:30分时（主力净额按源内占比换算，约值）"


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
        if response is None and method in ("MoneyFlow.ssc_bkzj_ssggzj", "MoneyFlow.ssl_bkzj_ssggzj"):
            response = self.request(BASE.replace("vip.stock.", "money.") + method, params,
                                    timeout=(3, 10), retries=1,
                                    headers={"Referer": "https://money.finance.sina.com.cn/"})
        if response is None:
            return None
        try:
            return response.json()
        except (ValueError, TypeError):
            return None

    def load(self):
        now = datetime.now(ZoneInfo("Asia/Shanghai"))
        today = now.strftime("%Y-%m-%d")
        if now.hour * 100 + now.minute < 1130:
            return pd.DataFrame()
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
                                 {"bankuai": category, "page": 1, "num": 6,
                                  "sort": "opendate", "asc": 0})
            if not isinstance(history, list) or not history or not isinstance(history[0], dict):
                return None
            try:
                # Daily history updates after close. Use it only to discard retired
                # classifications and for prior-session history, never as a noon quote.
                age = (datetime.fromisoformat(today) - datetime.fromisoformat(history[0]["opendate"])).days
                if not 0 <= age <= 14:
                    return None
                minutes = self._read("MoneyFlow.ssx_bkzj_fszs",
                                     {"bankuai": category, "page": 1, "num": 250, "sort": "time"})
                if not isinstance(minutes, list) or len(minutes) != 2 or not isinstance(minutes[1], list):
                    return None
                matching = [row for row in minutes[1] if isinstance(row, dict)
                            and row.get("opendate") == today and row.get("ticktime") == "11:30:00"]
                if len(matching) != 1:
                    return None
                latest = matching[0]
                net, net_ratio, ratio, change, price = [float(latest[key]) for key in
                    ("netamount", "ratioamount", "r0_ratio", "avg_changeratio", "avg_price")]
                # Both ratios use turnover as denominator. Sina rounds those ratios,
                # so explicitly label this result as derived rather than a raw quote.
                if not all(math.isfinite(v) for v in (net, net_ratio, ratio, change, price)):
                    return None
                if abs(net_ratio) < 0.00001 or abs(net_ratio) > 1 or abs(ratio) > 1:
                    return None
                turnover = net / net_ratio
                if turnover <= 0:
                    return None
                flow = turnover * ratio
                if not all(math.isfinite(v) for v in (flow, ratio, change, price)) or price <= 0:
                    return None
            except (KeyError, ValueError, TypeError):
                return None
            code = "sina:" + category
            self.history[code] = history
            return {"code": code, "name": item["name"], "price": price,
                    "change_pct": round(change * 100, 2), "main_net_inflow": round(flow / 1e8, 2),
                    "main_net_pct": round(ratio * 100, 2), "rise_count": None, "fall_count": None,
                    "source": SOURCE, "trade_date": today, "as_of": today + " 11:30",
                    "main_net_estimated": True}

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
        count = self._read("MoneyFlow.ssc_bkzj_ssggzj", {"bankuai": category})
        try:
            count = int(count)
        except (ValueError, TypeError):
            return set()
        if count <= 0 or count > 10000:
            return set()
        members = set()
        for page in range(1, math.ceil(count / 100) + 1):
            rows = self._read("MoneyFlow.ssl_bkzj_ssggzj",
                              {"bankuai": category, "page": page, "num": 100, "sort": "symbol", "asc": 1})
            if not isinstance(rows, list):
                return set()
            for row in rows:
                symbol = str(row.get("symbol") or "")
                if symbol[:2] in ("sh", "sz", "bj") and len(symbol) == 8 and symbol[2:].isdigit():
                    members.add(symbol[2:])
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
        rows = self.history.get(code, [])
        try:
            today = datetime.fromisoformat(self.date)
            rows = [row for row in rows if 0 < (today - datetime.fromisoformat(row["opendate"])).days <= 14][:days]
            flows = [float(row["r0_net"]) / 1e8 for row in rows]
            if not all(math.isfinite(value) for value in flows):
                flows = []
        except (KeyError, ValueError, TypeError):
            flows = []
        return {"recent_main_net_inflow": round(sum(flows), 2) if flows else None,
                "positive_days": sum(value > 0 for value in flows) if flows else None,
                "days": len(flows)}
