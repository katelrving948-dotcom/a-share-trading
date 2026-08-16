"""Incremental OHLCV acquisition for the independent factor research lane."""

from __future__ import annotations

import importlib
import json
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import pandas as pd


LOGGER = logging.getLogger(__name__)
PRICE_COLUMNS = ["date", "code", "name", "open", "high", "low", "close", "volume", "amount"]


@dataclass(frozen=True)
class QuantDataConfig:
    provider: str = "auto"
    universe_limit: int = 500
    include_bj: bool = False
    adjust: str = "hfq"
    max_workers: int = 6
    cache_dir: str = ".cache/quant_daily"


def _normalize_code(value) -> str:
    return str(value).split(".")[0].zfill(6)


class QuantDailyData:
    def __init__(self, config: QuantDataConfig | None = None):
        self.config = config or QuantDataConfig()
        self.cache_dir = Path(self.config.cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.provider_name = self._resolve_provider()
        self._module = importlib.import_module(self.provider_name)
        if self.provider_name == "tushare":
            self._module.set_token(os.environ["TUSHARE_TOKEN"])

    def _resolve_provider(self) -> str:
        requested = self.config.provider.lower().strip()
        if requested == "auto":
            requested = "tushare" if os.getenv("TUSHARE_TOKEN", "").strip() else "akshare"
        if requested not in {"akshare", "tushare"}:
            raise ValueError("QUANT_DATA_PROVIDER 仅支持 auto/akshare/tushare")
        if importlib.util.find_spec(requested) is None:
            raise RuntimeError(f"缺少数据依赖 {requested}，请先安装 requirements.txt")
        if requested == "tushare" and not os.getenv("TUSHARE_TOKEN", "").strip():
            raise RuntimeError("使用 Tushare 时必须设置 TUSHARE_TOKEN")
        return requested

    def list_universe(self) -> pd.DataFrame:
        if self.provider_name == "akshare":
            try:
                raw = self._module.stock_zh_a_spot_em()
                frame = raw.rename(columns={"代码": "code", "名称": "name", "成交额": "amount"})
            except Exception as exc:
                snapshot = Path(os.getenv("QUANT_UNIVERSE_SNAPSHOT", "output/research/fundamental_latest.json"))
                if not snapshot.exists():
                    raise RuntimeError(f"股票池接口失败且没有基本面快照：{exc}") from exc
                payload = json.loads(snapshot.read_text(encoding="utf-8"))
                frame = pd.DataFrame(payload.get("rows", []))
                if frame.empty or not {"code", "name"}.issubset(frame.columns):
                    raise RuntimeError("基本面快照不包含可用股票池") from exc
                frame["amount"] = 0.0
                LOGGER.warning("AkShare股票池接口失败，改用中午基本面快照：%s", exc)
        else:
            token = os.environ["TUSHARE_TOKEN"]
            pro = self._module.pro_api(token)
            raw = pro.stock_basic(
                exchange="", list_status="L", fields="ts_code,symbol,name,list_date"
            )
            frame = raw.rename(columns={"symbol": "code"})
            frame["amount"] = 0.0
            try:
                today = date.today()
                calendar = pro.trade_cal(
                    exchange="SSE",
                    start_date=(today - timedelta(days=14)).strftime("%Y%m%d"),
                    end_date=today.strftime("%Y%m%d"),
                    is_open="1",
                    fields="cal_date,is_open",
                )
                latest_trade_date = str(calendar["cal_date"].max())
                daily = pro.daily(trade_date=latest_trade_date, fields="ts_code,amount")
                daily["code"] = daily["ts_code"].map(_normalize_code)
                frame = frame.merge(daily[["code", "amount"]], on="code", how="left", suffixes=("", "_daily"))
                frame["amount"] = frame["amount_daily"].fillna(frame["amount"])
                frame = frame.drop(columns="amount_daily")
            except Exception as exc:
                LOGGER.warning("Tushare最新成交额不可用，股票池回退按代码排序: %s", exc)

        frame["code"] = frame["code"].map(_normalize_code)
        frame["name"] = frame["name"].astype(str)
        frame["amount"] = pd.to_numeric(frame.get("amount", 0), errors="coerce").fillna(0)
        frame = frame[~frame["name"].str.upper().str.contains("ST", na=False)]
        if not self.config.include_bj:
            frame = frame[~frame["code"].str.startswith(("4", "8"))]
        frame = frame.drop_duplicates("code").sort_values(["amount", "code"], ascending=[False, True])
        if self.config.universe_limit > 0:
            frame = frame.head(self.config.universe_limit)
        return frame[["code", "name"]].reset_index(drop=True)

    def _cache_path(self, code: str) -> Path:
        return self.cache_dir / f"{code}.csv"

    def _load_cache(self, code: str) -> pd.DataFrame:
        path = self._cache_path(code)
        if not path.exists():
            return pd.DataFrame(columns=PRICE_COLUMNS)
        try:
            frame = pd.read_csv(path, dtype={"code": str})
            frame["date"] = pd.to_datetime(frame["date"])
            frame["code"] = frame["code"].map(_normalize_code)
            return frame
        except (OSError, ValueError, KeyError) as exc:
            LOGGER.warning("忽略损坏的量化缓存 %s: %s", path, exc)
            return pd.DataFrame(columns=PRICE_COLUMNS)

    def _fetch_akshare(self, code: str, name: str, start: date, end: date) -> pd.DataFrame:
        source = os.getenv("AKSHARE_HISTORY_SOURCE", "auto").lower().strip()
        raw = None
        if source != "tx":
            try:
                raw = self._module.stock_zh_a_hist(
                    symbol=code,
                    period="daily",
                    start_date=start.strftime("%Y%m%d"),
                    end_date=end.strftime("%Y%m%d"),
                    adjust=self.config.adjust,
                    timeout=20,
                )
            except Exception:
                if source == "em":
                    raise
        if raw is None:
            prefix = "sh" if code.startswith(("6", "9")) else ("bj" if code.startswith(("4", "8")) else "sz")
            raw = self._module.stock_zh_a_hist_tx(
                symbol=f"{prefix}{code}",
                start_date=start.strftime("%Y%m%d"),
                end_date=end.strftime("%Y%m%d"),
                adjust=self.config.adjust,
                timeout=20,
            )
        if raw is None or raw.empty:
            return pd.DataFrame(columns=PRICE_COLUMNS)
        frame = raw.rename(columns={
            "日期": "date", "股票代码": "code", "开盘": "open", "最高": "high",
            "最低": "low", "收盘": "close", "成交量": "volume", "成交额": "amount",
            "turnover": "turnover_rate",
        })
        frame["code"] = code
        frame["name"] = name
        return frame[PRICE_COLUMNS]

    def _fetch_tushare(self, code: str, name: str, start: date, end: date) -> pd.DataFrame:
        suffix = "SH" if code.startswith(("6", "9")) else ("BJ" if code.startswith(("4", "8")) else "SZ")
        raw = self._module.pro_bar(
            ts_code=f"{code}.{suffix}",
            adj=self.config.adjust,
            start_date=start.strftime("%Y%m%d"),
            end_date=end.strftime("%Y%m%d"),
        )
        if raw is None or raw.empty:
            return pd.DataFrame(columns=PRICE_COLUMNS)
        frame = raw.rename(columns={
            "trade_date": "date", "vol": "volume", "ts_code": "provider_code",
        })
        frame["code"] = code
        frame["name"] = name
        frame["amount"] = pd.to_numeric(frame.get("amount", 0), errors="coerce").fillna(0)
        return frame[PRICE_COLUMNS]

    def _fetch_symbol(self, code: str, name: str, start: date, end: date) -> pd.DataFrame:
        cached = self._load_cache(code)
        fetch_start = start
        if not cached.empty:
            fetch_start = max(start, cached["date"].max().date() + timedelta(days=1))
        if fetch_start <= end:
            last_error = None
            for attempt in range(3):
                try:
                    fresh = (
                        self._fetch_akshare(code, name, fetch_start, end)
                        if self.provider_name == "akshare"
                        else self._fetch_tushare(code, name, fetch_start, end)
                    )
                    if not fresh.empty:
                        fresh["date"] = pd.to_datetime(fresh["date"])
                        cached = pd.concat([cached, fresh], ignore_index=True)
                    last_error = None
                    break
                except Exception as exc:  # provider errors vary by version/network
                    last_error = exc
                    time.sleep(0.5 * (attempt + 1))
            if last_error is not None:
                LOGGER.warning("%s 数据获取失败: %s", code, last_error)

        if cached.empty:
            return cached
        cached["date"] = pd.to_datetime(cached["date"], errors="coerce")
        cached = cached.dropna(subset=["date"])
        cached = cached.drop_duplicates(["date", "code"], keep="last")
        cached = cached.sort_values("date")
        cached.to_csv(self._cache_path(code), index=False, encoding="utf-8-sig")
        return cached[(cached["date"].dt.date >= start) & (cached["date"].dt.date <= end)]

    def fetch(self, start: date, end: date, symbols: list[str] | None = None) -> pd.DataFrame:
        if start > end:
            raise ValueError("start 不能晚于 end")
        if symbols:
            universe = pd.DataFrame({
                "code": [_normalize_code(code) for code in symbols],
                "name": [_normalize_code(code) for code in symbols],
            })
        else:
            universe = self.list_universe()
        if universe.empty:
            raise RuntimeError("未取得可用A股股票列表")

        LOGGER.info(
            "开始获取%s日线：%s只，%s 至 %s",
            self.provider_name, len(universe), start, end,
        )
        frames = []
        with ThreadPoolExecutor(max_workers=max(1, self.config.max_workers)) as executor:
            futures = {
                executor.submit(self._fetch_symbol, row.code, row.name, start, end): row.code
                for row in universe.itertuples(index=False)
            }
            for index, future in enumerate(as_completed(futures), start=1):
                frame = future.result()
                if not frame.empty:
                    frames.append(frame)
                if index % 50 == 0 or index == len(futures):
                    LOGGER.info("日线进度 %s/%s", index, len(futures))
        if not frames:
            raise RuntimeError("所有股票日线均获取失败，请检查网络、Token或数据源")
        result = pd.concat(frames, ignore_index=True)
        result["date"] = pd.to_datetime(result["date"])
        for column in ["open", "high", "low", "close", "volume", "amount"]:
            result[column] = pd.to_numeric(result[column], errors="coerce")
        return result.dropna(subset=["date", "code", "open", "high", "low", "close", "volume"])
