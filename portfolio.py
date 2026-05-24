"""
投资组合管理 - Portfolio
========================
独立的模拟交易引擎，不依赖 monitor.py。
"""

import json
import os
import pandas as pd
import numpy as np
from datetime import datetime, date as date_type
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, field, asdict

from config import RISK, BACKTEST


@dataclass
class Position:
    """持仓"""
    code: str
    name: str
    buy_price: float
    qty: int
    stop_loss: float = 0
    take_profit: float = 0
    strategy: str = ""
    buy_date: str = ""
    current_price: float = 0
    highest_price: float = 0

    @property
    def market_value(self) -> float:
        return self.qty * self.current_price

    @property
    def cost(self) -> float:
        return self.qty * self.buy_price

    @property
    def pnl(self) -> float:
        return self.market_value - self.cost

    @property
    def pnl_pct(self) -> float:
        if self.cost == 0:
            return 0.0
        return (self.current_price - self.buy_price) / self.buy_price * 100

    def update_price(self, price: float):
        self.current_price = price
        self.highest_price = max(self.highest_price, price)

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "name": self.name,
            "buy_price": round(self.buy_price, 2),
            "current_price": round(self.current_price, 2),
            "qty": self.qty,
            "cost": round(self.cost, 2),
            "market_value": round(self.market_value, 2),
            "pnl": round(self.pnl, 2),
            "pnl_pct": round(self.pnl_pct, 2),
            "stop_loss": round(self.stop_loss, 2),
            "take_profit": round(self.take_profit, 2),
            "strategy": self.strategy,
            "buy_date": self.buy_date,
        }


@dataclass
class Trade:
    """交易记录"""
    date: str
    type: str  # buy | sell
    code: str
    name: str
    price: float
    qty: int
    amount: float
    fee: float = 0
    reason: str = ""
    pnl: float = 0
    pnl_pct: float = 0


class Portfolio:
    """投资组合"""

    def __init__(self, data_dir: str = None):
        self.data_dir = data_dir or os.path.dirname(os.path.abspath(__file__))
        self.positions: Dict[str, Position] = {}
        self.trades: List[Trade] = []
        self.capital = BACKTEST["initial_capital"]
        self.initial_capital = BACKTEST["initial_capital"]
        self.watchlist: List[str] = []
        self._load()

    # ── 持久化 ──

    def _path(self, name: str) -> str:
        return os.path.join(self.data_dir, name)

    def _load(self):
        """从本地文件加载"""
        try:
            if os.path.exists(self._path("portfolio_positions.json")):
                with open(self._path("portfolio_positions.json")) as f:
                    data = json.load(f)
                self.capital = data.get("capital", self.initial_capital)
                self.initial_capital = data.get("initial_capital", self.initial_capital)
                for p in data.get("positions", []):
                    pos = Position(**p)
                    self.positions[pos.code] = pos
            if os.path.exists(self._path("portfolio_watchlist.json")):
                with open(self._path("portfolio_watchlist.json")) as f:
                    self.watchlist = json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            pass

    def _save(self):
        """保存到本地文件"""
        data = {
            "capital": self.capital,
            "initial_capital": self.initial_capital,
            "positions": [p.to_dict() for p in self.positions.values()],
        }
        with open(self._path("portfolio_positions.json"), "w") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        with open(self._path("portfolio_watchlist.json"), "w") as f:
            json.dump(self.watchlist, f, ensure_ascii=False, indent=2)

    # ── 交易操作 ──

    def buy(self, code: str, name: str, price: float, qty: int,
            strategy: str = "", stop_loss: float = 0,
            take_profit: float = 0) -> Tuple[bool, str]:
        """买入"""
        amount = qty * price
        fee = amount * BACKTEST["commission"]
        total_cost = amount + fee

        if total_cost > self.capital:
            return False, f"资金不足：需要¥{total_cost:.2f}，可用¥{self.capital:.2f}"

        today = datetime.now().strftime("%Y-%m-%d")
        pos = Position(
            code=code, name=name, buy_price=price, qty=qty,
            stop_loss=stop_loss or round(price * (1 + RISK["stop_loss"]), 2),
            take_profit=take_profit or round(price * (1 + RISK["take_profit"]), 2),
            strategy=strategy, buy_date=today,
            current_price=price, highest_price=price,
        )
        self.positions[code] = pos
        self.capital -= total_cost

        trade = Trade(date=today, type="买入", code=code, name=name,
                      price=price, qty=qty, amount=amount, fee=fee,
                      reason=strategy)
        self.trades.append(trade)
        self._save()
        return True, f"买入成功: {name} {qty}股 @¥{price:.2f}"

    def sell(self, code: str, price: float, qty: int = None,
             reason: str = "") -> Tuple[bool, str]:
        """卖出"""
        pos = self.positions.get(code)
        if not pos:
            return False, "未持仓此股票"

        sell_qty = qty or pos.qty
        if sell_qty > pos.qty:
            return False, f"持仓不足：持有{pos.qty}股，想卖{sell_qty}股"

        amount = sell_qty * price
        fee = amount * BACKTEST["commission"]
        tax = amount * BACKTEST["stamp_tax"]
        pnl = (price - pos.buy_price) * sell_qty - fee - tax
        pnl_pct = (price - pos.buy_price) / pos.buy_price * 100

        today = datetime.now().strftime("%Y-%m-%d")
        trade = Trade(date=today, type="卖出", code=code, name=pos.name,
                      price=price, qty=sell_qty, amount=amount,
                      fee=fee + tax, reason=reason,
                      pnl=round(pnl, 2), pnl_pct=round(pnl_pct, 2))
        self.trades.append(trade)
        self.capital += amount - fee - tax

        if sell_qty >= pos.qty:
            del self.positions[code]
        else:
            pos.qty -= sell_qty

        self._save()
        return True, f"卖出成功: {pos.name} {sell_qty}股 盈亏{pnl_pct:+.2f}%"

    def update_prices(self, quotes: Dict[str, float]):
        """批量更新持仓现价"""
        for code, price in quotes.items():
            if code in self.positions:
                self.positions[code].update_price(price)

    # ── 查询 ──

    def total_asset(self) -> float:
        return self.capital + sum(p.market_value for p in self.positions.values())

    def total_pnl(self) -> float:
        return self.total_asset() - self.initial_capital

    def total_pnl_pct(self) -> float:
        if self.initial_capital == 0:
            return 0.0
        return (self.total_asset() / self.initial_capital - 1) * 100

    def summary(self) -> dict:
        return {
            "initial_capital": round(self.initial_capital, 2),
            "available": round(self.capital, 2),
            "total_asset": round(self.total_asset(), 2),
            "total_pnl": round(self.total_pnl(), 2),
            "total_pnl_pct": round(self.total_pnl_pct(), 2),
            "position_count": len(self.positions),
            "position_value": round(sum(p.market_value for p in self.positions.values()), 2),
            "watchlist_count": len(self.watchlist),
        }

    def get_positions(self) -> List[dict]:
        return [p.to_dict() for p in self.positions.values()]

    def get_trades(self, limit: int = 50) -> List[dict]:
        recent = [asdict(t) for t in self.trades[-limit:]]
        recent.reverse()
        return recent

    # ── 自选股 ──

    def add_watchlist(self, code: str):
        if code not in self.watchlist:
            self.watchlist.append(code)
            self._save()

    def remove_watchlist(self, code: str):
        if code in self.watchlist:
            self.watchlist.remove(code)
            self._save()

    # ── 重置 ──

    def reset(self, capital: float = None):
        self.positions.clear()
        self.trades.clear()
        self.capital = capital or BACKTEST["initial_capital"]
        self.initial_capital = self.capital
        self._save()


# 全局实例
portfolio = Portfolio()
