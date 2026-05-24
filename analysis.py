"""
复盘分析模块 - Trade Analysis
===============================
交易记录分析、绩效评估、可视化报告。
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple
import os
import warnings
import traceback
warnings.filterwarnings("ignore")

from config import OUTPUT, BACKTEST, RISK

# 尝试导入matplotlib
try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
    plt.rcParams["font.sans-serif"] = ["SimHei", "WenQuanYi Micro Hei",
                                        "WenQuanYi Zen Hei", "Noto Sans CJK SC",
                                        "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    HAS_MPL = True
except ImportError:
    HAS_MPL = False


class Analyzer:
    """交易复盘分析器"""

    def __init__(self):
        self.chart_dir = OUTPUT["chart_dir"]

    def analyze_trades(self, trade_log: List[dict]) -> dict:
        """
        分析交易记录
        trade_log: 交易记录列表
        """
        if not trade_log:
            return {"error": "无交易记录"}

        df = pd.DataFrame(trade_log)

        # 仅分析卖出/平仓记录
        closes = df[df["类型"].isin(["卖出", "止损", "止盈", "期末平仓"])].copy()
        buy_records = df[df["类型"] == "买入"].copy()

        if closes.empty:
            return {"error": "无已完成交易"}

        # 基础统计
        total_trades = len(closes)
        win_trades = closes[closes["盈亏%"] > 0]
        loss_trades = closes[closes["盈亏%"] <= 0]
        win_count = len(win_trades)
        loss_count = len(loss_trades)

        # 盈亏统计
        total_pnl = closes["盈亏"].sum() if "盈亏" in closes.columns else 0
        total_pnl_pct = closes["盈亏%"].sum() if "盈亏%" in closes.columns else 0
        avg_win = win_trades["盈亏%"].mean() if win_count > 0 else 0
        avg_loss = loss_trades["盈亏%"].mean() if loss_count > 0 else 0
        max_win = win_trades["盈亏%"].max() if win_count > 0 else 0
        max_loss = loss_trades["盈亏%"].min() if loss_count > 0 else 0

        # 盈亏比
        profit_loss_ratio = abs(avg_win / avg_loss) if avg_loss != 0 else float("inf")

        # 持仓天数统计
        if "持仓天数" in closes.columns:
            avg_hold = closes["持仓天数"].mean()
            max_hold = closes["持仓天数"].max()
        else:
            avg_hold = max_hold = 0

        # 逐月统计
        if "卖出日期" in closes.columns:
            try:
                closes["月"] = pd.to_datetime(closes["卖出日期"]).dt.to_period("M")
                monthly = closes.groupby("月").agg(
                    交易次数=("盈亏%", "count"),
                    胜率=("盈亏%", lambda x: (x > 0).mean() * 100),
                    平均盈亏=("盈亏%", "mean"),
                    总盈亏=("盈亏%", "sum"),
                ).round(2)
            except Exception:
                monthly = pd.DataFrame()
        else:
            monthly = pd.DataFrame()

        # 策略分析
        strategy_stats = {}
        if "策略" in closes.columns:
            for strategy, group in closes.groupby("策略"):
                g_win = (group["盈亏%"] > 0).sum()
                g_total = len(group)
                strategy_stats[strategy] = {
                    "次数": g_total,
                    "胜率": round(g_win / g_total * 100, 1),
                    "平均盈亏": round(group["盈亏%"].mean(), 2),
                    "总盈亏": round(group["盈亏%"].sum(), 2),
                }

        # 最大回撤
        if "累计盈亏" in closes.columns:
            closes["peak"] = closes["累计盈亏"].cummax()
            closes["drawdown"] = closes["累计盈亏"] - closes["peak"]
            max_drawdown = closes["drawdown"].min()
        else:
            # 用盈亏%模拟
            closes["cum_pnl"] = closes["盈亏%"].cumsum()
            closes["peak"] = closes["cum_pnl"].cummax()
            closes["drawdown"] = closes["cum_pnl"] - closes["peak"]
            max_drawdown = closes["drawdown"].min()

        return {
            "总交易次数": total_trades,
            "盈利次数": win_count,
            "亏损次数": loss_count,
            "胜率": round(win_count / total_trades * 100, 1),
            "总盈亏": round(total_pnl, 2),
            "总盈亏%": round(total_pnl_pct, 2),
            "平均盈利%": round(avg_win, 2),
            "平均亏损%": round(avg_loss, 2),
            "最大盈利%": round(max_win, 2),
            "最大亏损%": round(max_loss, 2),
            "盈亏比": round(profit_loss_ratio, 2),
            "平均持仓天数": round(avg_hold, 1),
            "最长持仓天数": round(max_hold, 0),
            "最大回撤%": round(max_drawdown, 2),
            "月度统计": monthly,
            "策略分析": strategy_stats,
            "交易明细": closes,
        }

    def generate_report(self, trade_log: List[dict],
                        screen_result: pd.DataFrame = None,
                        output_file: str = None) -> str:
        """
        生成复盘报告HTML
        """
        if output_file is None:
            output_file = OUTPUT["analysis_report_file"]

        analysis = self.analyze_trades(trade_log)

        html = f"""
        <!DOCTYPE html>
        <html lang="zh-CN">
        <head>
        <meta charset="UTF-8">
        <title>A股交易复盘报告</title>
        <style>
            * {{ margin: 0; padding: 0; box-sizing: border-box; }}
            body {{ font-family: -apple-system, 'Microsoft YaHei', sans-serif;
                   background: #f5f6fa; color: #2d3436; padding: 20px; }}
            .container {{ max-width: 1000px; margin: 0 auto; }}
            h1 {{ color: #0984e3; margin-bottom: 20px; font-size: 28px; }}
            h2 {{ color: #2d3436; margin: 25px 0 15px; padding-bottom: 8px;
                  border-bottom: 2px solid #0984e3; font-size: 20px; }}
            .summary-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
                            gap: 15px; margin: 20px 0; }}
            .summary-card {{ background: white; border-radius: 10px; padding: 20px;
                            box-shadow: 0 2px 8px rgba(0,0,0,0.08); text-align: center; }}
            .summary-card .value {{ font-size: 28px; font-weight: bold; color: #0984e3; margin: 8px 0; }}
            .summary-card .value.positive {{ color: #00b894; }}
            .summary-card .value.negative {{ color: #d63031; }}
            .summary-card .label {{ font-size: 13px; color: #636e72; }}
            table {{ width: 100%; border-collapse: collapse; background: white;
                     border-radius: 10px; overflow: hidden; box-shadow: 0 2px 8px rgba(0,0,0,0.08); }}
            th {{ background: #0984e3; color: white; padding: 12px 15px;
                  text-align: left; font-size: 13px; }}
            td {{ padding: 10px 15px; border-bottom: 1px solid #dfe6e9; font-size: 13px; }}
            tr:hover td {{ background: #f8f9fa; }}
            .tag {{ display: inline-block; padding: 2px 10px; border-radius: 12px;
                    font-size: 12px; }}
            .tag-profit {{ background: #dfabd6; color: #00b894; }}
            .tag-loss {{ background: #fab1a0; color: #d63031; }}
            .footer {{ margin-top: 30px; text-align: center; color: #b2bec3; font-size: 12px; }}
        </style>
        </head>
        <body>
        <div class="container">
        <h1>📊 A股交易复盘报告</h1>
        <p style="color:#636e72; margin-bottom: 20px;">
            报告生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}
        </p>
        """

        if "error" in analysis:
            html += f'<p style="color:#d63031;">{analysis["error"]}</p>'
        else:
            # 摘要卡片
            html += '<h2>📈 绩效概览</h2><div class="summary-grid">'
            metrics = [
                ("总交易次数", str(analysis["总交易次数"]), ""),
                ("胜率", f'{analysis["胜率"]}%',
                 "positive" if analysis["胜率"] >= 50 else "negative"),
                ("总盈亏", f'{analysis["总盈亏%"]:+.2f}%',
                 "positive" if analysis["总盈亏%"] >= 0 else "negative"),
                ("盈亏比", str(analysis["盈亏比"]),
                 "positive" if analysis["盈亏比"] >= 1.5 else "negative"),
                ("平均盈利", f'{analysis["平均盈利%"]:+.2f}%', "positive"),
                ("平均亏损", f'{analysis["平均亏损%"]:+.2f}%', "negative"),
                ("最大盈利", f'{analysis["最大盈利%"]:+.2f}%', "positive"),
                ("最大亏损", f'{analysis["最大亏损%"]:+.2f}%', "negative"),
                ("平均持仓", f'{analysis["平均持仓天数"]}天', ""),
                ("最大回撤", f'{analysis["最大回撤%"]:.2f}%',
                 "negative" if analysis["最大回撤%"] < -10 else ""),
            ]
            for label, val, cls in metrics:
                html += (f'<div class="summary-card"><div class="label">{label}</div>'
                         f'<div class="value {cls}">{val}</div></div>')
            html += '</div>'

            # 交易明细
            df = analysis.get("交易明细", pd.DataFrame())
            if not df.empty:
                html += '<h2>📋 交易明细</h2><div style="overflow-x:auto;"><table><tr>'
                cols = ["卖出日期", "名称", "买入价", "卖出价", "盈亏%", "持仓天数", "策略", "卖出原因"]
                for c in cols:
                    if c in df.columns:
                        html += f"<th>{c}</th>"
                html += "</tr>"

                for _, r in df.iterrows():
                    pnl = r.get("盈亏%", 0)
                    tag = "tag-profit" if pnl > 0 else "tag-loss"
                    html += "<tr>"
                    for c in cols:
                        if c in df.index or c in df.columns:
                            val = r.get(c, "")
                            if c == "盈亏%":
                                html += f'<td><span class="tag {tag}">{val:+.2f}%</span></td>'
                            else:
                                html += f"<td>{val}</td>"
                    html += "</tr>"
                html += "</table></div>"

            # 月度统计
            monthly = analysis.get("月度统计", pd.DataFrame())
            if not monthly.empty:
                html += '<h2>📅 月度表现</h2><div style="overflow-x:auto;"><table><tr>'
                for c in monthly.columns:
                    html += f"<th>{c}</th>"
                html += "</tr>"
                for period, row in monthly.iterrows():
                    html += "<tr>"
                    html += f"<td>{period}</td>"
                    for val in row:
                        cls = "positive" if isinstance(val, (int, float)) and val > 0 else ""
                        html += f'<td class="{cls}">{val}</td>'
                    html += "</tr>"
                html += "</table></div>"

            # 策略分析
            strategy_stats = analysis.get("策略分析", {})
            if strategy_stats:
                html += '<h2>🎯 策略表现</h2><div style="overflow-x:auto;"><table><tr>'
                html += "<th>策略</th><th>次数</th><th>胜率</th><th>平均盈亏</th><th>总盈亏</th></tr>"
                for s, stats in sorted(strategy_stats.items(),
                                        key=lambda x: x[1]["总盈亏"], reverse=True):
                    cls = "positive" if stats["总盈亏"] > 0 else "negative"
                    html += f"<tr><td>{s[:30]}</td><td>{stats['次数']}</td>"
                    html += f"<td>{stats['胜率']}%</td>"
                    html += f"<td>{stats['平均盈亏']:+.2f}%</td>"
                    html += f'<td class="{cls}">{stats["总盈亏"]:+.2f}%</td></tr>'
                html += "</table></div>"

        html += f"""
        </div>
        <div class="footer">
            <p>⚠️ 本报告仅供参考，不构成投资建议。股市有风险，投资需谨慎。</p>
            <p>A股交易复盘系统 v1.0</p>
        </div>
        </body>
        </html>
        """

        # 保存HTML
        with open(output_file, "w", encoding="utf-8") as f:
            f.write(html)
        print(f"📄 报告已生成: {output_file}")
        return output_file

    def plot_equity_curve(self, equity_curve: List[dict],
                          title: str = "权益曲线",
                          save_path: str = None) -> Optional[str]:
        """绘制权益曲线"""
        if not HAS_MPL:
            print("⚠️ matplotlib不可用，跳过图表")
            return None

        if save_path is None:
            os.makedirs(self.chart_dir, exist_ok=True)
            save_path = os.path.join(self.chart_dir, "equity_curve.png")

        df = pd.DataFrame(equity_curve)
        if df.empty:
            return None

        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 8),
                                        gridspec_kw={"height_ratios": [3, 1]})

        # 权益曲线
        ax1.plot(df["date"], df["equity"], color="#0984e3", linewidth=1.5)
        positions = df[df["position"]]
        ax1.scatter(positions["date"], positions["equity"],
                   color="#00b894", s=8, alpha=0.5, label="持仓")
        ax1.axhline(y=df["equity"].iloc[0], color="#636e72",
                    linestyle="--", alpha=0.5, label="初始资金")
        ax1.set_title(title, fontsize=14, fontweight="bold")
        ax1.set_ylabel("账户权益", fontsize=11)
        ax1.legend(loc="upper left")
        ax1.grid(True, alpha=0.3)

        # 回撤曲线
        df["peak"] = df["equity"].cummax()
        df["drawdown"] = (df["equity"] - df["peak"]) / df["peak"] * 100
        ax2.fill_between(df["date"], df["drawdown"], 0,
                         color="#d63031", alpha=0.3)
        ax2.plot(df["date"], df["drawdown"], color="#d63031", linewidth=1)
        ax2.set_ylabel("回撤 %", fontsize=11)
        ax2.set_xlabel("日期", fontsize=11)
        ax2.grid(True, alpha=0.3)

        plt.tight_layout()
        fig_path = save_path
        plt.savefig(fig_path, dpi=150, bbox_inches="tight")
        plt.close(fig)
        print(f"📊 图表已保存: {fig_path}")
        return fig_path


if __name__ == "__main__":
    analyzer = Analyzer()
    # 测试数据
    test_trades = [
        {"卖出日期": "2026-01-15", "名称": "贵州茅台", "买入价": 1500,
         "卖出价": 1580, "盈亏%": 5.33, "持仓天数": 5, "策略": "放量突破"},
        {"卖出日期": "2026-01-20", "名称": "五粮液", "买入价": 130,
         "卖出价": 125, "盈亏%": -3.85, "持仓天数": 3, "策略": "MACD金叉"},
    ]
    report = analyzer.generate_report(test_trades)
    print(f"报告已生成: {report}")
