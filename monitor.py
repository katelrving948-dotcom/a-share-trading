"""
盘中监控模块 - Real-time Monitor
=================================
盘中实时监控持仓和候选股票，触发买卖信号时预警。
"""

import time
import threading
import pandas as pd
from datetime import datetime, time as dtime
from typing import List, Dict, Optional, Callable
from dataclasses import dataclass, field

from config import TRADING_HOURS, RISK
from data_feed import DataFeed
from strategy import StrategyEngine, TradeSignal, SignalType


@dataclass
class MonitorAlert:
    """监控预警"""
    type: str          # "buy_signal" | "sell_signal" | "price_alert"
    code: str
    name: str
    message: str
    price: float
    timestamp: datetime = field(default_factory=datetime.now)

    def __repr__(self):
        return (f"[{self.timestamp.strftime('%H:%M:%S')}] "
                f"{self.type}: {self.name}({self.code}) "
                f"@{self.price:.2f} → {self.message}")


class Position:
    """持仓管理"""

    def __init__(self, code: str, name: str, buy_price: float,
                 qty: int, stop_loss: float, take_profit: float,
                 strategy: str = ""):
        self.code = code
        self.name = name
        self.buy_price = buy_price
        self.qty = qty
        self.stop_loss = stop_loss
        self.take_profit = take_profit
        self.strategy = strategy
        self.buy_time = datetime.now()
        self.current_price = buy_price
        self.highest_price = buy_price
        self.pnl_pct = 0.0

    @property
    def market_value(self) -> float:
        return self.qty * self.current_price

    @property
    def cost(self) -> float:
        return self.qty * self.buy_price

    @property
    def pnl(self) -> float:
        return self.market_value - self.cost

    def update_price(self, price: float):
        """更新最新价"""
        self.current_price = price
        self.highest_price = max(self.highest_price, price)
        self.pnl_pct = (price - self.buy_price) / self.buy_price

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "name": self.name,
            "buy_price": self.buy_price,
            "current_price": self.current_price,
            "qty": self.qty,
            "cost": self.cost,
            "market_value": self.market_value,
            "pnl": self.pnl,
            "pnl_pct": self.pnl_pct,
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "strategy": self.strategy,
            "buy_time": self.buy_time.strftime("%Y-%m-%d %H:%M"),
        }


class TradeManager:
    """交易管理器 - 持仓/资金/交易记录"""

    def __init__(self, initial_capital: float = 100000):
        self.capital = initial_capital
        self.initial_capital = initial_capital
        self.positions: Dict[str, Position] = {}
        self.trade_history: List[dict] = []
        self.buy_signals: List[TradeSignal] = []
        self.sell_signals: List[TradeSignal] = []
        self.max_positions = RISK["max_positions"]
        self.max_trades_per_day = RISK["max_trades_per_day"]
        self.trade_count_today = 0
        self._reset_day = datetime.now().date()

    def available_capital(self) -> float:
        """可用资金"""
        return self.capital

    def total_asset(self) -> float:
        """总资产"""
        return self.capital + sum(p.market_value for p in self.positions.values())

    def can_open_position(self) -> bool:
        """是否可以开新仓"""
        # 检查持仓数量
        if len(self.positions) >= self.max_positions:
            return False
        # 检查每日交易次数
        self._check_day_reset()
        if self.trade_count_today >= self.max_trades_per_day:
            return False
        # 检查可用资金
        if self.available_capital() < 5000:
            return False
        return True

    def open_position(self, signal: TradeSignal, qty: int) -> Optional[Position]:
        """开仓"""
        if not self.can_open_position():
            return None

        amount = qty * signal.price
        if amount > self.available_capital():
            return None

        position = Position(
            code=signal.code,
            name=signal.name,
            buy_price=signal.price,
            qty=qty,
            stop_loss=signal.stop_loss,
            take_profit=signal.take_profit,
            strategy=signal.reason,
        )
        self.positions[signal.code] = position
        self.capital -= amount

        # 记录交易
        self.trade_count_today += 1
        self.trade_history.append({
            "时间": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "类型": "买入",
            "代码": signal.code,
            "名称": signal.name,
            "价格": signal.price,
            "数量": qty,
            "金额": amount,
            "策略": signal.reason,
        })
        return position

    def close_position(self, code: str, signal: TradeSignal) -> Optional[dict]:
        """平仓"""
        position = self.positions.pop(code, None)
        if not position:
            return None

        amount = position.qty * signal.price
        self.capital += amount
        pnl = amount - position.cost

        record = {
            "时间": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "类型": signal.signal.value,
            "代码": code,
            "名称": position.name,
            "买入价": position.buy_price,
            "卖出价": signal.price,
            "数量": position.qty,
            "金额": amount,
            "盈亏": round(pnl, 2),
            "盈亏%": round((signal.price - position.buy_price) / position.buy_price * 100, 2),
            "持仓天数": (datetime.now() - position.buy_time).days,
            "策略": position.strategy,
            "卖出原因": signal.reason,
        }
        self.trade_history.append(record)

        self.trade_count_today += 1
        return record

    def _check_day_reset(self):
        """检查是否需要重置当日计数"""
        today = datetime.now().date()
        if today != self._reset_day:
            self.trade_count_today = 0
            self._reset_day = today

    def summary(self) -> dict:
        """账户摘要"""
        total_pnl = sum(p.pnl for p in self.positions.values())
        total_invested = sum(p.cost for p in self.positions.values())
        return {
            "初始资金": self.initial_capital,
            "总资产": self.total_asset(),
            "可用资金": self.capital,
            "持仓市值": sum(p.market_value for p in self.positions.values()),
            "总盈亏": self.total_asset() - self.initial_capital,
            "总收益率": (self.total_asset() / self.initial_capital - 1) * 100,
            "持仓数量": len(self.positions),
            "今日交易": self.trade_count_today,
        }


class Monitor:
    """盘中实时监控系统"""

    def __init__(self, trade_manager: TradeManager,
                 alert_callback: Optional[Callable] = None):
        self.trade_manager = trade_manager
        self.df = DataFeed()
        self.strategy = StrategyEngine()
        self.alert_callback = alert_callback
        self.alerts: List[MonitorAlert] = []
        self._watch_list: List[str] = []

    def set_watch_list(self, codes: List[str]):
        """设置观察列表"""

        self._watch_list = codes

    def check_once(self) -> List[MonitorAlert]:
        """执行一次检查（适用于定时调用）"""
        alerts = []
        codes_to_check = list(self._watch_list)
        # 加入持仓股票
        codes_to_check.extend(p.code for p in self.trade_manager.positions.values()
                              if p.code not in codes_to_check)

        if not codes_to_check:
            return alerts

        # 获取实时行情
        quotes = self.df.get_realtime_quotes(codes_to_check)
        if quotes.empty:
            return alerts

        # 更新持仓价格
        for _, q in quotes.iterrows():
            code = q["code"]
            if code in self.trade_manager.positions:
                self.trade_manager.positions[code].update_price(q["price"])

        # 检查持仓卖出信号
        for code, position in list(self.trade_manager.positions.items()):
            # 获取实时K线
            kline = self.df.get_kline(code, count=30)
            pos_dict = position.to_dict()
            pos_dict["current_price"] = position.current_price

            signal = self.strategy.generate_sell_signals(kline, pos_dict)
            if signal:
                alert = MonitorAlert("sell_signal", code, position.name,
                                     str(signal), position.current_price)
                alerts.append(alert)
                self.alerts.append(alert)

        # 检查观察列表买入信号
        for code in self._watch_list:
            if code in self.trade_manager.positions:
                continue
            q = quotes[quotes["code"] == code]
            if q.empty:
                continue

            kline = self.df.get_kline(code, count=30)
            stock_info = {
                "code": code,
                "name": q.iloc[0]["name"],
                "price": q.iloc[0]["price"],
            }
            signal = self.strategy.generate_buy_signals(kline, stock_info)
            if signal:
                alert = MonitorAlert("buy_signal", code, stock_info["name"],
                                     str(signal), q.iloc[0]["price"])
                alerts.append(alert)
                self.alerts.append(alert)

        for alert in alerts:
            print(alert)
            if self.alert_callback:
                self.alert_callback(alert)

        return alerts

    def is_trading_time(self) -> bool:
        """判断是否在交易时间"""
        now = datetime.now()
        if now.weekday() >= 5:  # 周末
            return False

        t = now.time()
        morning_start = dtime(9, 30)
        morning_end = dtime(11, 30)
        afternoon_start = dtime(13, 0)
        afternoon_end = dtime(15, 0)

        if morning_start <= t <= morning_end:
            return True
        if afternoon_start <= t <= afternoon_end:
            return True
        return False

    def format_positions(self) -> str:
        """格式化输出持仓"""
        lines = ["\n" + "=" * 60,
                 f"📋 当前持仓 ({datetime.now().strftime('%H:%M:%S')})",
                 "-" * 60]
        if not self.trade_manager.positions:
            lines.append("  空仓")
        else:
            for p in self.trade_manager.positions.values():
                lines.append(f"  {p.code} {p.name:<8s} "
                             f"成本:{p.buy_price:.2f} 现价:{p.current_price:.2f} "
                             f"盈亏:{p.pnl_pct*100:+.2f}%  "
                             f"止损:{p.stop_loss:.2f} 止盈:{p.take_profit:.2f}")

        lines.extend([
            "-" * 60,
            f"💰 总资产:{self.trade_manager.total_asset():.2f} "
            f"可用:{self.trade_manager.available_capital():.2f}",
            "=" * 60,
        ])
        return "\n".join(lines)


def run_monitor_loop(interval: int = 30):
    """
    启动盘中监控循环
    interval: 检查间隔（秒）
    """
    tm = TradeManager()
    monitor = Monitor(tm)

    print(f"🚀 盘中监控启动 (每{interval}秒检查一次)")
    print(f"⏰ 交易时间: {TRADING_HOURS['morning_start']}-"
          f"{TRADING_HOURS['morning_end']}, "
          f"{TRADING_HOURS['afternoon_start']}-"
          f"{TRADING_HOURS['afternoon_end']}")
    print(f"📊 监控持仓+观察列表\n")

    try:
        while True:
            if monitor.is_trading_time():
                alerts = monitor.check_once()
                if monitor.trade_manager.positions:
                    print(monitor.format_positions())
            else:
                print(f"\r⏳ 非交易时间，等待开盘...", end="")

            time.sleep(interval)
    except KeyboardInterrupt:
        print("\n\n🛑 监控已停止")
        print(monitor.trade_manager.summary())


if __name__ == "__main__":
    # 测试运行
    tm = TradeManager()
    monitor = Monitor(tm)
    monitor.set_watch_list(["600519", "000858", "002415"])
    print("测试检查...")
    monitor.check_once()
