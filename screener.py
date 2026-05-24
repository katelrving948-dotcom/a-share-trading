"""
选股模块 - Stock Screener
=========================
基于多因子评分系统全市场筛选短线标的。
"""

import pandas as pd
import numpy as np
from typing import List, Dict, Optional
from datetime import datetime, timedelta
import time

from config import SCREEN, INDICATORS, STRATEGY
from data_feed import DataFeed


class StockScreener:
    """A股多因子选股器"""

    def __init__(self):
        self.df = DataFeed()

    def screen(self) -> pd.DataFrame:
        """
        执行全市场选股
        返回: 评分排序后的股票列表
        """
        print("=" * 60)
        print("📊 A股短线选股系统")
        print(f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        print("=" * 60)

        # Step 1: 获取全市场数据
        stocks = self.df.get_stock_list()
        if stocks.empty:
            print("❌ 获取股票列表失败")
            return pd.DataFrame()
        print(f"📈 全市场股票: {len(stocks)} 只")

        # Step 2: 基础过滤
        filtered = self._basic_filter(stocks)
        print(f"✅ 通过基础过滤: {len(filtered)} 只")

        # Step 3: 获取K线数据并计算技术指标评分
        scored = self._score_stocks(filtered)
        print(f"📊 完成技术指标评分")

        # Step 4: 排序输出
        result = scored.sort_values("综合评分", ascending=False)
        result = result.head(STRATEGY["max_stocks"])

        print(f"\n🏆 推荐标的 Top {len(result)}:\n")
        if not result.empty:
            for i, (_, row) in enumerate(result.iterrows(), 1):
                print(f"  {i:2d}. {row['code']} {row['name']:>8s}  "
                      f"评分:{row['综合评分']:.0f}  "
                      f"现价:{row['price']:.2f}  "
                      f"涨幅:{row['change_pct']:+.2f}%  "
                      f"市值:{row['market_cap']:.0f}亿  "
                      f"换手:{row['turnover_rate']:.1f}%")
        else:
            print("  暂无符合条件标的")

        return result

    def _basic_filter(self, stocks: pd.DataFrame) -> pd.DataFrame:
        """基础条件过滤"""
        df = stocks.copy()

        conditions = (
            (df["price"] >= SCREEN["price_min"]) &
            (df["price"] <= SCREEN["price_max"]) &
            (df["market_cap"] >= SCREEN["market_cap_min"]) &
            (df["market_cap"] <= SCREEN["market_cap_max"]) &
            (df["amount"] >= SCREEN["avg_amount_min"] * 1e8) &
            (df["turnover_rate"] >= SCREEN["turnover_min"]) &
            (df["turnover_rate"] <= SCREEN["turnover_max"])
        )

        if SCREEN["exclude_st"]:
            conditions &= ~df["is_st"]
        if SCREEN["exclude_kcb"]:
            conditions &= (df["board"] != "科创板")
        if SCREEN["exclude_bj"]:
            conditions &= (df["board"] != "北交所")

        return df[conditions].copy()

    def _score_stocks(self, stocks: pd.DataFrame) -> pd.DataFrame:
        """对股票进行多因子评分"""
        scores = []
        weights = STRATEGY["strategy_weights"]
        batch_size = 50
        codes = stocks["code"].tolist()

        for i in range(0, len(codes), batch_size):
            batch = codes[i:i+batch_size]
            batch_stocks = stocks[stocks["code"].isin(batch)]

            for _, stock in batch_stocks.iterrows():
                try:
                    score = self._single_score(stock, weights)
                    if score["综合评分"] >= STRATEGY["score_threshold"]:
                        scores.append(score)
                except Exception:
                    continue

            # 打印进度
            pct = min(100, (i + batch_size) / len(codes) * 100)
            print(f"\r📊 评分进度: {pct:.0f}% ({min(i+batch_size, len(codes))}/{len(codes)})",
                  end="", flush=True)
            time.sleep(0.5)

        print()
        if not scores:
            return pd.DataFrame()

        result = pd.DataFrame(scores)
        return result

    def _single_score(self, stock: pd.Series,
                      weights: Dict[str, float]) -> Dict:
        """单只股票评分"""
        code = stock["code"]
        name = stock["name"]
        detail = {
            "code": code, "name": name,
            "price": stock["price"],
            "change_pct": stock["change_pct"],
            "market_cap": stock["market_cap"],
            "turnover_rate": stock["turnover_rate"],
            "volume_ratio": stock.get("volume_ratio", 1),
        }

        # 获取K线
        kline = self.df.get_kline(code, count=60)
        if kline.empty or len(kline) < 20:
            detail["综合评分"] = 0
            return detail

        latest = kline.iloc[-1]
        pre = kline.iloc[-2] if len(kline) > 1 else latest

        scores = {}

        # ── 1. 放量突破评分 ──
        vol_ratio = latest.get("volume", 0) / max(latest.get("VOL_MA5", 1), 1)
        vol_score = 0
        if vol_ratio >= INDICATORS["volume_ratio"]:
            # 放量且收阳
            if latest["close"] > latest["open"]:
                vol_score = min(100, (vol_ratio / 3) * 100)
                # 突破MA20加分
                if (latest["close"] > latest.get("MA20", 99999) and
                        pre.get("close", 0) <= pre.get("MA20", 0)):
                    vol_score = min(100, vol_score + 20)
        scores["放量突破"] = vol_score

        # ── 2. 均线金叉评分 ──
        ma_score = 0
        if (not pd.isna(latest.get("MA5")) and not pd.isna(latest.get("MA10")) and
                not pd.isna(pre.get("MA5")) and not pd.isna(pre.get("MA10"))):
            # MA5上穿MA10
            if (latest["MA5"] > latest["MA10"] and pre["MA5"] <= pre["MA10"]):
                ma_score = 90
            # MA5在MA10上方（多头排列）
            elif latest["MA5"] > latest["MA10"]:
                ma_score = 60
            elif latest["MA5"] > latest["MA10"] and latest["MA10"] > latest.get("MA20", 0):
                ma_score = 75
        scores["均线金叉"] = ma_score

        # ── 3. MACD评分 ──
        macd_score = 0
        if (not pd.isna(latest.get("MACD_DIF")) and
                not pd.isna(latest.get("MACD_DEA")) and
                not pd.isna(pre.get("MACD_DIF")) and
                not pd.isna(pre.get("MACD_DEA"))):
            # DIF上穿DEA（金叉）
            if (latest["MACD_DIF"] > latest["MACD_DEA"] and
                    pre["MACD_DIF"] <= pre["MACD_DEA"]):
                macd_score = 90
            # DIF在DEA上方
            elif latest["MACD_DIF"] > latest["MACD_DEA"]:
                macd_score = 60
            # 零轴上方金叉（强势）
            if (latest["MACD_DIF"] > 0 and latest["MACD_DEA"] > 0 and
                    macd_score >= 60):
                macd_score = min(100, macd_score + 10)
        scores["MACD"] = macd_score

        # ── 4. KDJ评分 ──
        kdj_score = 0
        if (not pd.isna(latest.get("KDJ_K")) and
                not pd.isna(latest.get("KDJ_D")) and
                not pd.isna(pre.get("KDJ_K")) and
                not pd.isna(pre.get("KDJ_D"))):
            # K上穿D（金叉）
            if (latest["KDJ_K"] > latest["KDJ_D"] and
                    pre["KDJ_K"] <= pre["KDJ_D"]):
                kdj_score = 85
            # K在D上方
            elif latest["KDJ_K"] > latest["KDJ_D"]:
                kdj_score = 55
            # 低位金叉（超卖区域 < 30）
            if latest["KDJ_K"] < 40 and kdj_score >= 50:
                kdj_score = min(100, kdj_score + 15)
        scores["KDJ"] = kdj_score

        # ── 5. 量价配合评分 ──
        vp_score = 0
        if latest["close"] > latest["open"]:  # 阳线
            vp_score += 40
        if latest["close"] > pre["close"]:     # 价格上涨
            vp_score += 20
        if latest.get("volume", 0) > pre.get("volume", 0):  # 放量
            vp_score += 20
        if latest["close"] > max(latest.get("MA5", 0), latest.get("MA10", 0)):
            vp_score += 20
        scores["量价配合"] = min(100, vp_score)

        # ── 综合评分 ──
        total = (scores.get("放量突破", 0) * weights["volume_breakout"] +
                 scores.get("均线金叉", 0) * weights["ma_golden_cross"] +
                 scores.get("MACD", 0) * weights["macd_signal"] +
                 scores.get("KDJ", 0) * weights["kdj_signal"] +
                 scores.get("量价配合", 0) * weights["volume_price"])

        detail.update({
            "放量突破": scores.get("放量突破", 0),
            "均线金叉": scores.get("均线金叉", 0),
            "MACD": scores.get("MACD", 0),
            "KDJ": scores.get("KDJ", 0),
            "量价配合": scores.get("量价配合", 0),
            "综合评分": round(total),
        })
        return detail


if __name__ == "__main__":
    screener = StockScreener()
    result = screener.screen()
    if not result.empty:
        result.to_csv("选股结果.csv", index=False, encoding="utf-8-sig")
        print(f"\n结果已保存至: 选股结果.csv")
