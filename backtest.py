"""
回测模块 - Backtest Engine
===========================
历史K线回测，验证策略有效性。
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple
import warnings
warnings.filterwarnings("ignore")

from config import BACKTEST, INDICATORS
from data_feed import DataFeed
from strategy import StrategyEngine, SignalType


class BacktestEngine:
    """策略回测引擎"""

    def __init__(self, initial_capital: float = None):
        self.df = DataFeed()
        self.strategy = StrategyEngine()
        self.initial_capital = initial_capital or BACKTEST["initial_capital"]
        self.commission = BACKTEST["commission"]
        self.stamp_tax = BACKTEST["stamp_tax"]
        self.slippage = BACKTEST["slippage"]
        self.trade_log: List[dict] = []

    def run(self, code: str, name: str = "",
            start_date: str = "", days: int = 365) -> Optional[dict]:
        """
        对单只股票执行回测
        code: 股票代码
        days: 回测天数
        """
        print(f"\n📊 回测: {code} {name}  周期:{days}天")

        # 获取K线
        kline = self.df.get_kline(code, count=min(days, 800))
        if kline.empty or len(kline) < 30:
            print(f"  ⚠️ 数据不足，跳过")
            return None

        # 初始化
        capital = self.initial_capital
        available = capital
        position_qty = 0
        position_price = 0
        position_date = None
        position_stop = 0
        position_take = 0
        self.trade_log = []
        equity_curve = []
        signals_count = 0
        win_count = 0

        # 逐日回测
        for i in range(20, len(kline)):
            current = kline.iloc[i]
            prev = kline.iloc[i-1] if i > 0 else current
            date = current["date"]
            close = current["close"]
            high = current["high"]
            low = current["low"]

            # 构建滚动K线
            kline_up_to = kline.iloc[:i+1].reset_index(drop=True)

            # ── 持仓中 → 检查卖出 ──
            if position_qty > 0:
                position_info = {
                    "code": code,
                    "name": name,
                    "buy_price": position_price,
                    "current_price": close,
                    "stop_loss": position_stop,
                    "take_profit": position_take,
                    "qty": position_qty,
                }
                sell_signal = self.strategy.generate_sell_signals(
                    kline_up_to, position_info
                )

                if sell_signal:
                    # 执行卖出
                    sell_price = close * (1 - self.slippage)
                    amount = position_qty * sell_price
                    tax = amount * self.stamp_tax if sell_price > position_price else 0
                    fee = amount * self.commission
                    pnl = amount - position_qty * position_price - fee - tax
                    available += amount - fee - tax

                    if pnl > 0:
                        win_count += 1

                    self.trade_log.append({
                        "买入日期": position_date,
                        "卖出日期": date,
                        "代码": code,
                        "名称": name,
                        "方向": sell_signal.signal.value,
                        "买入价": round(position_price, 2),
                        "卖出价": round(sell_price, 2),
                        "数量": position_qty,
                        "盈亏": round(pnl, 2),
                        "盈亏%": round((sell_price - position_price) / position_price * 100, 2),
                        "策略": sell_signal.reason,
                    })

                    position_qty = 0
                    signals_count += 1

            # ── 空仓 → 检查买入 ──
            if position_qty == 0:
                stock_info = {"code": code, "name": name, "price": close}
                buy_signal = self.strategy.generate_buy_signals(
                    kline_up_to, stock_info
                )
                if buy_signal and buy_signal.signal == SignalType.BUY:
                    # 执行买入
                    buy_price = close * (1 + self.slippage)
                    max_qty = int(available * 0.95 / buy_price / 100) * 100
                    if max_qty >= 100:
                        position_qty = max_qty
                        position_price = buy_price
                        position_date = date
                        position_stop = buy_price * (1 + self.strategy.stop_loss_pct)
                        position_take = buy_price * (1 + self.strategy.take_profit_pct)

                        cost = position_qty * buy_price
                        fee = cost * self.commission
                        available -= cost + fee
                        signals_count += 1

            # 记录权益曲线
            equity = available
            if position_qty > 0:
                equity += position_qty * close
            equity_curve.append({
                "date": date,
                "equity": equity,
                "position": position_qty > 0,
                "price": close,
            })

        # ── 结仓 ──
        if position_qty > 0:
            sell_price = kline.iloc[-1]["close"] * (1 - self.slippage)
            amount = position_qty * sell_price
            fee = amount * self.commission
            tax = amount * self.stamp_tax if sell_price > position_price else 0
            pnl = amount - position_qty * position_price
            available += amount - fee - tax

            self.trade_log.append({
                "买入日期": position_date,
                "卖出日期": kline.iloc[-1]["date"],
                "代码": code,
                "名称": name,
                "方向": "期末平仓",
                "买入价": round(position_price, 2),
                "卖出价": round(sell_price, 2),
                "数量": position_qty,
                "盈亏": round(pnl, 2),
                "盈亏%": round((sell_price - position_price) / position_price * 100, 2),
                "策略": "期末强制平仓",
            })

        # ── 计算绩效指标 ──
        result = self._calc_performance(equity_curve, capital)
        result["code"] = code
        result["name"] = name
        result["trades"] = self.trade_log
        result["equity_curve"] = equity_curve

        print(f"  总收益率:{result['total_return']:+.2f}%  "
              f"年化:{result['annual_return']:+.2f}%  "
              f"胜率:{result['win_rate']:.1f}%  "
              f"交易次数:{signals_count}  "
              f"最大回撤:{result['max_drawdown']:.2f}%")
        return result

    def _calc_performance(self, equity_curve: List[dict],
                          initial: float) -> dict:
        """计算绩效指标"""
        if not equity_curve:
            return {"total_return": 0, "annual_return": 0,
                    "max_drawdown": 0, "win_rate": 0,
                    "sharpe_ratio": 0, "trade_count": 0}

        df_eq = pd.DataFrame(equity_curve)
        final = df_eq["equity"].iloc[-1]
        total_return = (final / initial - 1) * 100

        # 年化收益率
        days = len(df_eq)
        annual_return = ((final / initial) ** (252 / max(days, 1)) - 1) * 100

        # 最大回撤
        df_eq["peak"] = df_eq["equity"].cummax()
        df_eq["drawdown"] = (df_eq["equity"] - df_eq["peak"]) / df_eq["peak"] * 100
        max_drawdown = df_eq["drawdown"].min()

        # 胜率
        trades = self.trade_log
        win_count = sum(1 for t in trades if t.get("盈亏%", 0) > 0)
        trade_count = len(trades)

        # 夏普比率
        df_eq["return"] = df_eq["equity"].pct_change().fillna(0)
        sharpe = (df_eq["return"].mean() / max(df_eq["return"].std(), 0.001)) * np.sqrt(252)

        # 盈亏比
        if trade_count > 0:
            avg_win = np.mean([t["盈亏%"] for t in trades if t["盈亏%"] > 0]) if win_count > 0 else 0
            avg_loss = np.mean([t["盈亏%"] for t in trades if t["盈亏%"] <= 0]) if (trade_count - win_count) > 0 else 0
            profit_loss_ratio = abs(avg_win / avg_loss) if avg_loss != 0 else 0
        else:
            profit_loss_ratio = 0

        return {
            "total_return": round(total_return, 2),
            "annual_return": round(annual_return, 2),
            "max_drawdown": round(max_drawdown, 2),
            "win_rate": round(win_count / max(trade_count, 1) * 100, 1),
            "sharpe_ratio": round(sharpe, 2),
            "trade_count": trade_count,
            "profit_loss_ratio": round(profit_loss_ratio, 2),
        }

    def run_multi(self, codes: List[str], days: int = 365) -> pd.DataFrame:
        """批量回测多只股票"""
        results = []
        for code in codes:
            # 获取股票名称
            stocks = self.df.get_stock_list()
            name = ""
            if not stocks.empty:
                match = stocks[stocks["code"] == code]
                if not match.empty:
                    name = match.iloc[0]["name"]

            result = self.run(code, name, days=days)
            if result:
                results.append(result)

        if not results:
            return pd.DataFrame()

        summary = pd.DataFrame([{
            "代码": r["code"],
            "名称": r["name"],
            "总收益率%": r["total_return"],
            "年化收益率%": r["annual_return"],
            "最大回撤%": r["max_drawdown"],
            "胜率%": r["win_rate"],
            "夏普比率": r["sharpe_ratio"],
            "交易次数": r["trade_count"],
            "盈亏比": r["profit_loss_ratio"],
        } for r in results])

        return summary.sort_values("总收益率%", ascending=False)


if __name__ == "__main__":
    bt = BacktestEngine()
    result = bt.run("600519", "贵州茅台", days=250)
    if result:
        print(f"\n📋 交易记录 ({len(result['trades'])}笔):")
        for t in result["trades"][-5:]:
            print(f"  {t['买入日期']}→{t['卖出日期']} {t['方向']} "
                  f"盈亏:{t['盈亏%']:+.2f}%")
