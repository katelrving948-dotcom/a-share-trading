"""
交易策略模块 - Trading Strategy
===============================
买卖信号生成、仓位管理、止盈止损规则。
"""

import pandas as pd
import numpy as np
from typing import List, Dict, Optional, Tuple
from datetime import datetime
from enum import Enum

from config import INDICATORS, RISK


class SignalType(Enum):
    """交易信号类型"""
    BUY = "买入"
    SELL = "卖出"
    HOLD = "持有"
    STOP_LOSS = "止损"
    TAKE_PROFIT = "止盈"


class TradeSignal:
    """交易信号"""

    def __init__(self, code: str, name: str, signal: SignalType,
                 price: float, reason: str, score: float = 0,
                 stop_loss: float = 0, take_profit: float = 0):
        self.code = code
        self.name = name
        self.signal = signal
        self.price = price
        self.reason = reason
        self.score = score
        self.stop_loss = stop_loss
        self.take_profit = take_profit
        self.timestamp = datetime.now()

    def __repr__(self):
        return (f"[{self.signal.value}] {self.code} {self.name} "
                f"@{self.price:.2f} | {self.reason}")


class StrategyEngine:
    """策略引擎 - 买卖信号生成"""

    def __init__(self):
        self.position_size = RISK["position_pct"]
        self.stop_loss_pct = RISK["stop_loss"]
        self.take_profit_pct = RISK["take_profit"]

    def generate_buy_signals(self, kline: pd.DataFrame,
                              stock_info: dict) -> Optional[TradeSignal]:
        """
        生成买入信号
        kline: 日K线DataFrame (含技术指标)
        stock_info: {"code", "name", "price", ...}
        """
        if kline.empty or len(kline) < 30:
            return None

        signals = []
        latest = kline.iloc[-1]
        pre = kline.iloc[-2] if len(kline) > 1 else latest

        code = stock_info.get("code", "")
        name = stock_info.get("name", "")
        price = float(stock_info.get("price", latest["close"]))

        # ── 策略1: 放量突破 ──
        sig = self._check_volume_breakout(latest, pre, kline)
        if sig:
            signals.append((sig, 90))

        # ── 策略2: 均线金叉 ──
        sig = self._check_ma_cross(latest, pre)
        if sig:
            signals.append((sig, 80))

        # ── 策略3: MACD金叉 ──
        sig = self._check_macd_cross(latest, pre)
        if sig:
            signals.append((sig, 85))

        # ── 策略4: KDJ低位金叉 ──
        sig = self._check_kdj_cross(latest, pre)
        if sig:
            signals.append((sig, 75))

        if not signals:
            return None

        # 综合判断 - 取最高可信度的信号
        best_signal, confidence = max(signals, key=lambda x: x[1])

        # 计算止盈止损价
        stop_loss = round(price * (1 + self.stop_loss_pct), 2)
        take_profit = round(price * (1 + self.take_profit_pct), 2)

        return TradeSignal(
            code=code, name=name,
            signal=SignalType.BUY,
            price=price,
            reason=best_signal,
            score=confidence,
            stop_loss=stop_loss,
            take_profit=take_profit,
        )

    def generate_sell_signals(self, kline: pd.DataFrame,
                               position: dict) -> Optional[TradeSignal]:
        """
        生成卖出信号
        position: {"code", "name", "buy_price", "current_price",
                   "stop_loss", "take_profit", "qty", ...}
        """
        current_price = position["current_price"]
        buy_price = position["buy_price"]
        pnl_pct = (current_price - buy_price) / buy_price

        # ── 止损检查 ──
        if pnl_pct <= self.stop_loss_pct:
            return TradeSignal(
                code=position["code"],
                name=position["name"],
                signal=SignalType.STOP_LOSS,
                price=current_price,
                reason=f"止损触发: 盈亏{pnl_pct*100:.1f}% ≤ {self.stop_loss_pct*100:.0f}%",
                stop_loss=position.get("stop_loss", 0),
                take_profit=position.get("take_profit", 0),
            )

        # ── 止盈检查 ──
        if pnl_pct >= self.take_profit_pct:
            return TradeSignal(
                code=position["code"],
                name=position["name"],
                signal=SignalType.TAKE_PROFIT,
                price=current_price,
                reason=f"止盈触发: 盈亏{pnl_pct*100:.1f}% ≥ {self.take_profit_pct*100:.0f}%",
                stop_loss=position.get("stop_loss", 0),
                take_profit=position.get("take_profit", 0),
            )

        # ── 技术卖出信号 ──
        if kline.empty or len(kline) < 10:
            return None

        latest = kline.iloc[-1]
        pre = kline.iloc[-2] if len(kline) > 1 else latest
        sell_reasons = []

        # MACD死叉
        if (not pd.isna(latest.get("MACD_DIF")) and
                not pd.isna(latest.get("MACD_DEA")) and
                not pd.isna(pre.get("MACD_DIF")) and
                not pd.isna(pre.get("MACD_DEA"))):
            if latest["MACD_DIF"] < latest["MACD_DEA"] and pre["MACD_DIF"] >= pre["MACD_DEA"]:
                sell_reasons.append("MACD死叉")

        # 跌破MA10
        if (not pd.isna(latest.get("MA10")) and
                latest["close"] < latest["MA10"] and
                pre.get("close", 0) >= pre.get("MA10", 0)):
            sell_reasons.append("跌破MA10")

        # 死叉 + 跌破均线
        if len(sell_reasons) >= 2:
            return TradeSignal(
                code=position["code"],
                name=position["name"],
                signal=SignalType.SELL,
                price=current_price,
                reason="; ".join(sell_reasons),
                stop_loss=position.get("stop_loss", 0),
                take_profit=position.get("take_profit", 0),
            )

        return None

    # ──────────── 信号检测方法 ────────────

    def _check_volume_breakout(self, latest: pd.Series,
                                pre: pd.Series,
                                kline: pd.DataFrame) -> Optional[str]:
        """检测放量突破信号"""
        # 成交量条件
        vol_ma5 = latest.get("VOL_MA5", 0)
        if pd.isna(vol_ma5) or vol_ma5 <= 0:
            return None
        vol_ratio = latest["volume"] / vol_ma5
        if vol_ratio < INDICATORS["volume_ratio"]:
            return None

        # 价格条件：收阳线
        if latest["close"] <= latest["open"]:
            return None

        # 突破均线
        ma20 = latest.get("MA20", 0)
        ma60 = latest.get("MA60", 0)
        if pd.isna(ma20):
            return None

        reasons = [f"放量{vol_ratio:.1f}倍"]

        if latest["close"] > ma20 and pre.get("close", 0) <= pre.get("MA20", 0):
            reasons.append("突破MA20")

        if (not pd.isna(ma60) and ma60 > 0 and
                latest["close"] > ma60 and pre.get("close", 0) <= pre.get("MA60", 0)):
            reasons.append("突破MA60")

        if len(reasons) >= 2:
            return "放量突破:" + "+".join(reasons)
        elif vol_ratio >= 2.0:
            return "放量突破:" + f"量比{vol_ratio:.1f}倍"
        return None

    def _check_ma_cross(self, latest: pd.Series,
                        pre: pd.Series) -> Optional[str]:
        """检测均线金叉"""
        if (pd.isna(latest.get("MA5")) or pd.isna(latest.get("MA10")) or
                pd.isna(pre.get("MA5")) or pd.isna(pre.get("MA10"))):
            return None

        # MA5上穿MA10
        if (latest["MA5"] > latest["MA10"] and
                pre["MA5"] <= pre["MA10"]):
            return "MA5金叉MA10"

        # 多头排列
        if (latest["MA5"] > latest["MA10"] and
                latest["MA10"] > latest.get("MA20", 0)):
            return "多头排列"
        return None

    def _check_macd_cross(self, latest: pd.Series,
                          pre: pd.Series) -> Optional[str]:
        """检测MACD金叉"""
        if (pd.isna(latest.get("MACD_DIF")) or
                pd.isna(latest.get("MACD_DEA")) or
                pd.isna(pre.get("MACD_DIF")) or
                pd.isna(pre.get("MACD_DEA"))):
            return None

        if (latest["MACD_DIF"] > latest["MACD_DEA"] and
                pre["MACD_DIF"] <= pre["MACD_DEA"]):
            if latest["MACD_DIF"] > 0:
                return "MACD零轴上方金叉(强势)"
            else:
                return "MACD零轴下方金叉(反弹)"
        return None

    def _check_kdj_cross(self, latest: pd.Series,
                         pre: pd.Series) -> Optional[str]:
        """检测KDJ低位金叉"""
        if (pd.isna(latest.get("KDJ_K")) or
                pd.isna(latest.get("KDJ_D")) or
                pd.isna(pre.get("KDJ_K")) or
                pd.isna(pre.get("KDJ_D"))):
            return None

        # K上穿D
        if (latest["KDJ_K"] > latest["KDJ_D"] and
                pre["KDJ_K"] <= pre["KDJ_D"]):
            if latest["KDJ_K"] < 30:
                return "KDJ超卖区域金叉"
            elif latest["KDJ_K"] < 50:
                return "KDJ中低位金叉"
            else:
                return "KDJ金叉"
        return None

    # ──────────── 仓位管理 ────────────

    def calc_position_size(self, capital: float, price: float,
                           score: float) -> Tuple[int, float]:
        """
        计算买入数量
        capital: 可用资金
        price: 当前价格
        score: 策略评分 (0-100)
        返回: (股数, 买入金额)
        """
        # 评分越高，仓位越重
        position_ratio = self.position_size * min(1.0, score / 70)
        target_amount = capital * position_ratio

        # A股交易单位: 100股（手）
        qty = max(100, int(target_amount / price / 100) * 100)
        amount = qty * price

        # 确保不超过可用资金
        if amount > capital:
            qty = int(capital / price / 100) * 100
            amount = qty * price

        return qty, amount

    def calc_sell_qty(self, position_qty: int,
                      signal: SignalType) -> int:
        """计算卖出数量"""
        if signal in (SignalType.STOP_LOSS, SignalType.TAKE_PROFIT):
            return position_qty  # 全仓卖出
        elif signal == SignalType.SELL:
            return position_qty  # 技术卖出也全仓
        return 0
