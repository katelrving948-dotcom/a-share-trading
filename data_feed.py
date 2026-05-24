"""
数据获取模块 - Data Feed
========================
通过东方财富/新浪财经免费API获取A股数据。
无需额外安装数据包，依赖 requests + pandas。
"""

import time
import json
import re
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Tuple

from config import REQUEST_TIMEOUT, REQUEST_RETRIES, REQUEST_INTERVAL, REQUEST_TIMEOUT as _


class DataFeed:
    """A股数据接口 - 东方财富 + 新浪财经"""

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/120.0.0.0 Safari/537.36"),
            "Referer": "https://quote.eastmoney.com/",
        })
        self._stock_list_cache = None
        self._last_request_time = 0

    def _rate_limit(self):
        """请求频率控制"""
        elapsed = time.time() - self._last_request_time
        if elapsed < REQUEST_INTERVAL:
            time.sleep(REQUEST_INTERVAL - elapsed)
        self._last_request_time = time.time()

    def _request(self, url: str, params: dict = None) -> Optional[requests.Response]:
        """带重试的请求"""
        for attempt in range(REQUEST_RETRIES):
            try:
                self._rate_limit()
                resp = self.session.get(url, params=params, timeout=REQUEST_TIMEOUT)
                resp.encoding = "utf-8"
                if resp.status_code == 200:
                    return resp
            except requests.RequestException:
                if attempt < REQUEST_RETRIES - 1:
                    time.sleep(1)
        return None

    # ──────────── 股票列表 ────────────

    def get_stock_list(self, force_refresh: bool = False) -> pd.DataFrame:
        """
        获取全A股股票列表（东方财富）
        返回: DataFrame 包含 code, name, 上市日期等
        """
        if self._stock_list_cache is not None and not force_refresh:
            return self._stock_list_cache

        url = "https://push2.eastmoney.com/api/qt/clist/get"
        all_stocks = []

        # 沪深: 主板+创业板
        for market, fs in [("沪深", "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048"),
                           ("北交", "m:0+t:83+s:2048")]:
            page = 1
            while True:
                params = {
                    "pn": page,
                    "pz": 500,
                    "po": 1,
                    "np": 1,
                    "ut": "bd1d9ddb04089700cf9c27f6f7426281",
                    "fltt": 2,
                    "invt": 2,
                    "fid": "f3",
                    "fs": fs,
                    "fields": ("f12,f14,f3,f4,f2,f15,f16,f17,f18,"
                               "f20,f21,f8,f9,f10,f5,f6,f7,f13,f37"),
                }
                resp = self._request(url, params)
                if not resp:
                    break

                try:
                    data = resp.json()
                    items = data.get("data", {}).get("diff", [])
                    if not items:
                        break
                    for item in items:
                        all_stocks.append({
                            "code": str(item.get("f12", "")),
                            "name": item.get("f14", ""),
                            "market": market,
                            "price": item.get("f2", 0) or 0,
                            "change_pct": item.get("f3", 0) or 0,
                            "change_amt": item.get("f4", 0) or 0,
                            "high": item.get("f15", 0) or 0,
                            "low": item.get("f16", 0) or 0,
                            "volume": item.get("f5", 0) or 0,
                            "amount": item.get("f6", 0) or 0,
                            "turnover_rate": item.get("f37", 0) or 0,
                            "amplitude": item.get("f7", 0) or 0,
                            "market_cap": (item.get("f20", 0) or 0) / 1e8,
                            "total_market_cap": (item.get("f21", 0) or 0) / 1e8,
                            "pe": item.get("f9", 0) or 0,
                            "volume_ratio": item.get("f10", 0) or 0,
                            "change_handsup": item.get("f8", 0) or 0,
                        })
                    page += 1
                except (json.JSONDecodeError, KeyError):
                    break

        result = pd.DataFrame(all_stocks)
        if result.empty:
            return result

        # 类型转换
        num_cols = ["price", "change_pct", "change_amt", "high", "low",
                     "volume", "amount", "turnover_rate", "amplitude",
                     "market_cap", "total_market_cap", "pe", "volume_ratio"]
        for col in num_cols:
            if col in result.columns:
                result[col] = pd.to_numeric(result[col], errors="coerce")

        # 识别板块
        def _get_board(code):
            if code.startswith("8") or code.startswith("4"):
                return "北交所"
            if code.startswith("3"):
                return "创业板"
            if code.startswith("68"):
                return "科创板"
            if code.startswith("0") or code.startswith("1"):
                return "深圳主板"
            if code.startswith("6"):
                return "上海主板"
            return "其他"

        result["board"] = result["code"].apply(_get_board)

        # 识别ST
        result["is_st"] = result["name"].str.contains("ST|退", na=False)

        self._stock_list_cache = result
        return result

    # ──────────── 历史K线 ────────────

    def get_kline(self, code: str, period: str = "day",
                  count: int = 120) -> pd.DataFrame:
        """
        获取日K/周K/月K线数据（东方财富）
        period: day | week | month
        count: 获取的K线根数
        返回包含 MA5, MA10, MA20, MA60, MACD, RSI, KDJ
        """
        SECID_MAP = {"1": "SH", "0": "SZ"}
        secid = self._get_secid(code)
        if not secid:
            return pd.DataFrame()

        period_map = {"day": "101", "week": "102", "month": "103"}
        klt = period_map.get(period, "101")

        url = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
        params = {
            "secid": secid,
            "ut": "fa5fd1943c7b386f172d6893dbfd32bb",
            "fields1": "f1,f2,f3,f4,f5,f6",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
            "klt": klt,
            "fqt": "1",  # 前复权
            "end": "20500101",
            "lmt": count,
        }
        resp = self._request(url, params)
        if not resp:
            return pd.DataFrame()

        try:
            data = resp.json()
            klines = data.get("data", {}).get("klines", [])
        except (json.JSONDecodeError, KeyError, AttributeError):
            return pd.DataFrame()

        rows = []
        for line in klines:
            parts = line.split(",")
            if len(parts) >= 11:
                rows.append({
                    "date": parts[0],
                    "open": float(parts[1]),
                    "close": float(parts[2]),
                    "high": float(parts[3]),
                    "low": float(parts[4]),
                    "volume": float(parts[5]),
                    "amount": float(parts[6]),
                    "amplitude": float(parts[7]),
                    "change_pct": float(parts[8]),
                    "change_amt": float(parts[9]),
                    "turnover": float(parts[10]),
                })

        df = pd.DataFrame(rows)
        if df.empty:
            return df

        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date").reset_index(drop=True)

        # 计算技术指标
        df = self._calc_indicators(df)
        return df

    def _calc_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """计算常用技术指标"""
        close = df["close"].values
        high = df["high"].values
        low = df["low"].values
        volume = df["volume"].values

        # ── 均线 ──
        for period in [5, 10, 20, 60]:
            if len(close) >= period:
                df[f"MA{period}"] = pd.Series(
                    np.convolve(close, np.ones(period)/period, mode="valid"),
                    index=df.index[period-1:]
                )
            else:
                df[f"MA{period}"] = np.nan

        # ── MACD ──
        ema_fast = self._ema(close, 12)
        ema_slow = self._ema(close, 26)
        dif = ema_fast - ema_slow
        dea = self._ema(dif, 9)
        macd_bar = 2 * (dif - dea)
        df["MACD_DIF"] = dif
        df["MACD_DEA"] = dea
        df["MACD_BAR"] = macd_bar

        # ── RSI ──
        period = 14
        if len(close) > period:
            delta = np.diff(close)
            gains = np.where(delta > 0, delta, 0)
            losses = np.where(delta < 0, -delta, 0)
            avg_gain = self._ema(gains, period)
            avg_loss = self._ema(losses, period)
            rs = avg_gain / np.maximum(avg_loss, 1e-10)
            rsi = 100 - (100 / (1 + rs))
            df["RSI"] = np.concatenate([np.full(1, np.nan), rsi])
        else:
            df["RSI"] = np.nan

        # ── KDJ ──
        period_k = 9
        if len(close) >= period_k:
            low_n = pd.Series(low).rolling(period_k).min().values
            high_n = pd.Series(high).rolling(period_k).max().values
            rsv = np.where(
                (high_n - low_n) != 0,
                (close - low_n) / (high_n - low_n) * 100,
                50
            )
            k = self._ema(rsv, 3)
            d = self._ema(k, 3)
            j = 3 * k - 2 * d
            df["KDJ_K"] = k
            df["KDJ_D"] = d
            df["KDJ_J"] = j
        else:
            df["KDJ_K"] = np.nan
            df["KDJ_D"] = np.nan
            df["KDJ_J"] = np.nan

        # ── 成交量均线 ──
        if len(volume) >= 5:
            df["VOL_MA5"] = pd.Series(
                np.convolve(volume, np.ones(5)/5, mode="valid"),
                index=df.index[4:]
            )
        else:
            df["VOL_MA5"] = np.nan
        if len(volume) >= 10:
            df["VOL_MA10"] = pd.Series(
                np.convolve(volume, np.ones(10)/10, mode="valid"),
                index=df.index[9:]
            )
        else:
            df["VOL_MA10"] = np.nan

        # ── BOLL ──
        period_boll = 20
        if len(close) >= period_boll:
            mid = pd.Series(close).rolling(period_boll).mean().values
            std = pd.Series(close).rolling(period_boll).std().values
            df["BOLL_MID"] = mid
            df["BOLL_UP"] = mid + 2 * std
            df["BOLL_DN"] = mid - 2 * std
        else:
            df["BOLL_MID"] = df["BOLL_UP"] = df["BOLL_DN"] = np.nan

        return df

    @staticmethod
    def _ema(values: np.ndarray, period: int) -> np.ndarray:
        """指数移动平均"""
        alpha = 2 / (period + 1)
        result = np.full_like(values, np.nan, dtype=float)
        if len(values) == 0:
            return result
        result[0] = values[0]
        for i in range(1, len(values)):
            if np.isnan(result[i-1]):
                result[i] = values[i]
            else:
                result[i] = alpha * values[i] + (1 - alpha) * result[i-1]
        return result

    # ──────────── 实时行情 ────────────

    def get_realtime_quotes(self, codes: List[str]) -> pd.DataFrame:
        """
        获取实时行情（新浪财经）
        codes: 股票代码列表，如 ["600519", "000858"]
        """
        if not codes:
            return pd.DataFrame()

        # 构建新浪格式代码
        sina_codes = []
        code_map = {}
        for code in codes:
            prefix = "sh" if code.startswith(("6", "9")) else "sz"
            s_code = f"{prefix}{code}"
            sina_codes.append(s_code)
            code_map[s_code] = code

        url = "https://hq.sinajs.cn/list=" + ",".join(sina_codes)
        self.session.headers.update({"Referer": "https://finance.sina.com.cn"})
        resp = self._request(url)
        if not resp:
            return pd.DataFrame()

        rows = []
        for line in resp.text.strip().split("\n"):
            if not line:
                continue
            try:
                match = re.search(r'hq_str_(\w+)="(.+)"', line)
                if not match:
                    continue
                s_code = match.group(1)
                values = match.group(2).split(",")
                if len(values) < 32:
                    continue
                rows.append({
                    "code": code_map.get(s_code, s_code),
                    "name": values[0],
                    "open": float(values[1]) if values[1] else 0,
                    "close_yest": float(values[2]) if values[2] else 0,
                    "price": float(values[3]) if values[3] else 0,
                    "high": float(values[4]) if values[4] else 0,
                    "low": float(values[5]) if values[5] else 0,
                    "volume": float(values[8]) if values[8] else 0,
                    "amount": float(values[9]) if values[9] else 0,
                })
            except (ValueError, IndexError):
                continue

        df = pd.DataFrame(rows)
        if not df.empty:
            df["change_pct"] = ((df["price"] - df["close_yest"])
                                / df["close_yest"].replace(0, np.nan) * 100)
        return df

    # ──────────── 辅助方法 ────────────

    def _get_secid(self, code: str) -> Optional[str]:
        """获取东方财富secid格式"""
        if code.startswith(("6", "9")):
            return f"1.{code}"
        elif code.startswith(("0", "3", "2")):
            return f"0.{code}"
        elif code.startswith(("4", "8")):
            return f"0.{code}"
        return None

    def get_stock_info(self, code: str) -> dict:
        """获取单只股票基本信息"""
        url = "https://push2.eastmoney.com/api/qt/stock/get"
        secid = self._get_secid(code)
        if not secid:
            return {}
        params = {
            "secid": secid,
            "ut": "fa5fd1943c7b386f172d6893dbfd32bb",
            "fields": "f43,f44,f45,f46,f47,f48,f49,f50,f51,f52,f55,f57,f58,f84,f85",
        }
        resp = self._request(url, params)
        if not resp:
            return {}
        try:
            d = resp.json().get("data", {}) or {}
            return {
                "code": code,
                "open": d.get("f44", 0),
                "close": d.get("f43", 0),
                "high": d.get("f45", 0),
                "low": d.get("f46", 0),
                "volume": d.get("f47", 0),
                "amount": d.get("f48", 0),
                "pe": d.get("f57", 0),
                "amplitude": d.get("f43", 0),
            }
        except (json.JSONDecodeError, KeyError, TypeError):
            return {}

    def get_concept_board(self) -> pd.DataFrame:
        """获取概念板块列表（用于板块热度分析）"""
        url = "https://push2.eastmoney.com/api/qt/clist/get"
        params = {
            "pn": 1, "pz": 500, "po": 1, "np": 1,
            "ut": "bd1d9ddb04089700cf9c27f6f7426281",
            "fltt": 2, "invt": 2, "fid": "f3", "fs": "m:90+t:3",
            "fields": "f12,f14,f2,f3,f4,f8,f20",
        }
        resp = self._request(url, params)
        if not resp:
            return pd.DataFrame()
        try:
            items = resp.json().get("data", {}).get("diff", [])
            rows = [{
                "code": i.get("f12"),
                "name": i.get("f14"),
                "price": i.get("f2"),
                "change_pct": i.get("f3"),
                "rise_count": i.get("f8"),
                "total_market_cap": i.get("f20", 0),
            } for i in items if i.get("f12")]
            return pd.DataFrame(rows)
        except (json.JSONDecodeError, KeyError, TypeError):
            return pd.DataFrame()


if __name__ == "__main__":
    df = DataFeed()
    print("→ 获取股票列表中...")
    stocks = df.get_stock_list()
    print(f"共获取 {len(stocks)} 只股票")
    print(stocks.head(10).to_string())

    print("\n→ 获取贵州茅台K线...")
    kline = df.get_kline("600519", count=30)
    print(kline.tail(5).to_string())
