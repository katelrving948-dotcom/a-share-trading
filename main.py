#!/usr/bin/env python3
"""
A股短线交易系统 - 主入口
=========================
全流程覆盖: 盘前选股 → 盘中监控 → 盘后复盘

用法:
    python main.py screen       盘前选股
    python main.py monitor      盘中监控
    python main.py backtest     回测策略
    python main.py analyze      分析交易记录
    python main.py full         全流程执行
    python main.py watch        监控指定股票列表
"""

import sys
import os
import pandas as pd
from datetime import datetime
from typing import Optional

from config import OUTPUT, RISK
from data_feed import DataFeed
from screener import StockScreener
from strategy import StrategyEngine, SignalType
from monitor import Monitor, TradeManager
from backtest import BacktestEngine
from analysis import Analyzer


def print_banner():
    """打印启动横幅"""
    print("""
    ╔══════════════════════════════════════╗
    ║        📊 A股 短线交易系统            ║
    ║    盘前选股 · 盘中监控 · 盘后复盘      ║
    ╚══════════════════════════════════════╝
    """)


def cmd_screen():
    """盘前选股"""
    print_banner()
    print(f"⏰ 选股时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n")

    screener = StockScreener()
    result = screener.screen()

    if result.empty:
        print("\n❌ 今日无符合条件标的")
        return

    # 保存结果
    output_file = OUTPUT["screen_result_file"]
    result.to_csv(output_file, index=False, encoding="utf-8-sig")
    print(f"\n💾 选股结果已保存: {output_file}")

    # 生成简要HTML
    html = _result_to_html(result)
    html_file = "选股结果.html"
    with open(html_file, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"💾 可视化结果: {html_file}")

    # 询问是否加入观察列表
    codes = result["code"].tolist()
    print(f"\n📋 可将以上 {len(codes)} 只股票加入盘中监控观察列表")


def cmd_monitor():
    """盘中监控模式"""
    print_banner()

    # 先加载选股结果
    df_watch = pd.DataFrame()
    if os.path.exists(OUTPUT["screen_result_file"]):
        df_watch = pd.read_csv(OUTPUT["screen_result_file"])
        print(f"📂 已加载选股结果: {len(df_watch)} 只股票\n")
    else:
        print("⚠️  未检测到今日选股结果，将只监控持仓\n")

    tm = TradeManager(initial_capital=RISK["position_pct"] * 1000000)
    monitor = Monitor(tm)

    if not df_watch.empty:
        codes = df_watch["code"].tolist()
        monitor.set_watch_list(codes)
        print(f"🔭 观察列表: {len(codes)} 只股票")

    print(f"💰 初始资金: {tm.initial_capital:.2f}")
    print(f"📊 仓位管理: 每票{RISK['position_pct']*100:.0f}% "
          f"止损{RISK['stop_loss']*100:.0f}% "
          f"止盈{RISK['take_profit']*100:.0f}%")
    print(f"⚠️ 注意: 此为模拟监控模式\n")

    # 执行单次检查
    print("→ 执行单次检查...")
    alerts = monitor.check_once()

    if alerts:
        print(f"\n🚨 发现 {len(alerts)} 条预警:")
        for a in alerts:
            print(f"  {a}")
    else:
        print("  暂无信号触发")

    if monitor.trade_manager.positions:
        print(monitor.format_positions())

    print("\n💡 提示: 如需持续监控请运行 python main.py watch")
    return monitor


def cmd_watch(interval: int = 60):
    """持续监控模式"""
    from monitor import run_monitor_loop

    print_banner()
    print("🚀 启动持续盘中监控...")

    # 加载选股结果作为观察列表
    monitor_instance = cmd_monitor()
    if monitor_instance:
        print("\n⏳ 进入循环监控...")
        try:
            import time
            while True:
                time.sleep(interval)
                alerts = monitor_instance.check_once()
                if monitor_instance.trade_manager.positions:
                    print(monitor_instance.format_positions())
        except KeyboardInterrupt:
            print("\n🛑 监控已停止")
            summary = monitor_instance.trade_manager.summary()
            for k, v in summary.items():
                print(f"  {k}: {v:.2f}" if isinstance(v, float) else f"  {k}: {v}")


def cmd_backtest():
    """回测模式"""
    print_banner()
    print("📊 策略回测\n")

    # 从选股结果或手动输入
    codes = []
    if os.path.exists(OUTPUT["screen_result_file"]):
        df = pd.read_csv(OUTPUT["screen_result_file"])
        codes = df["code"].tolist()[:10]
        print(f"📂 从选股结果加载 {len(codes)} 只股票进行回测")
    else:
        # 默认回测标的
        codes = ["600519", "000858", "002415", "300750", "601318"]
        print(f"📂 使用默认标的: {', '.join(codes)}")

    bt = BacktestEngine()
    results = bt.run_multi(codes, days=120)

    if not results.empty:
        print("\n" + "=" * 60)
        print("📋 回测结果汇总")
        print("=" * 60)
        print(results.to_string(index=False))
        print("=" * 60)

        # 保存
        results.to_csv("回测汇总.csv", index=False, encoding="utf-8-sig")
        print("\n💾 回测结果已保存: 回测汇总.csv")

        # 详细回测报告
        print("\n📄 生成详细回测报告...")
        for _, row in results.iterrows():
            code = row["代码"]
            bt.run(code, "", days=120)
    else:
        print("❌ 回测失败或数据不足")


def cmd_analyze():
    """分析交易记录"""
    print_banner()
    print("📊 交易复盘分析\n")

    # 读取交易记录
    if os.path.exists(OUTPUT["trade_log_file"]):
        df = pd.read_csv(OUTPUT["trade_log_file"])
        trades = df.to_dict("records")
        print(f"📂 加载交易记录: {len(trades)} 条")
    else:
        print(f"⚠️  未找到交易记录文件: {OUTPUT['trade_log_file']}")
        print("  请先运行 monitor 模式进行交易")
        return

    analyzer = Analyzer()
    report = analyzer.generate_report(trades)
    print(f"\n✅ 复盘报告已生成: {report}")


def cmd_full():
    """全流程执行: 选股 → 回测 → 分析"""
    print_banner()
    print("🔄 全流程执行\n")

    # Step 1: 选股
    print("=" * 60)
    print("Step 1/3: 📊 盘前选股")
    print("=" * 60)
    screener = StockScreener()
    result = screener.screen()

    if result.empty:
        print("\n❌ 今日无符合条件的标的，流程终止")
        return

    result.to_csv(OUTPUT["screen_result_file"], index=False, encoding="utf-8-sig")
    print(f"\n✅ 选股完成，共 {len(result)} 只标的")

    # Step 2: 回测验证
    print("\n" + "=" * 60)
    print("Step 2/3: 📈 回测验证")
    print("=" * 60)

    codes = result["code"].tolist()[:5]
    bt = BacktestEngine()
    backtest_results = bt.run_multi(codes, days=120)

    if backtest_results is not None and not backtest_results.empty:
        backtest_results.to_csv("回测汇总.csv", index=False, encoding="utf-8-sig")
        print(f"\n✅ 回测完成")
    else:
        print("⚠️  回测数据不足")

    # Step 3: 模拟复盘
    print("\n" + "=" * 60)
    print("Step 3/3: 📋 模拟复盘分析")
    print("=" * 60)

    analyzer = Analyzer()
    # 收集所有回测交易记录
    all_trades = []
    for code in codes[:3]:
        bt_result = bt.run(code, "", days=120)
        if bt_result and bt_result.get("trades"):
            all_trades.extend(bt_result["trades"])

    if all_trades:
        report = analyzer.generate_report(all_trades)
        print(f"\n✅ 复盘报告已生成: {report}")
    else:
        print("⚠️  无交易记录生成报告")

    print("\n" + "=" * 60)
    print("✅ 全流程执行完毕")
    print("=" * 60)


def _result_to_html(df: pd.DataFrame) -> str:
    """选股结果转HTML"""
    if df.empty:
        return "<p>暂无结果</p>"

    rows = ""
    for _, r in df.iterrows():
        pct = r.get("change_pct", 0)
        cls = "positive" if pct >= 0 else "negative"
        rows += f"""<tr>
            <td>{r.get("code", "")}</td>
            <td>{r.get("name", "")}</td>
            <td>{r.get("price", 0):.2f}</td>
            <td class="{cls}">{pct:+.2f}%</td>
            <td>{r.get("market_cap", 0):.0f}</td>
            <td>{r.get("turnover_rate", 0):.1f}%</td>
            <td>{r.get("综合评分", 0):.0f}</td>
        </tr>"""

    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8">
<title>A股选股结果</title>
<style>
    body {{ font-family: 'Microsoft YaHei', sans-serif; background: #f5f6fa; padding: 20px; }}
    h1 {{ color: #0984e3; }}
    table {{ width: 100%; border-collapse: collapse; background: white;
             border-radius: 10px; overflow: hidden; box-shadow: 0 2px 8px rgba(0,0,0,0.08); }}
    th {{ background: #0984e3; color: white; padding: 12px; text-align: left; }}
    td {{ padding: 10px 12px; border-bottom: 1px solid #dfe6e9; }}
    .positive {{ color: #d63031; }} .negative {{ color: #00b894; }}
    tr:hover {{ background: #f8f9fa; }}
    .time {{ color: #636e72; margin: 10px 0; }}
</style></head><body>
<h1>📊 A股选股结果</h1>
<p class="time">{datetime.now().strftime('%Y-%m-%d %H:%M')} 生成</p>
<table><tr><th>代码</th><th>名称</th><th>现价</th><th>涨幅</th>
    <th>市值(亿)</th><th>换手率</th><th>评分</th></tr>
{rows}</table>
<p style="color:#b2bec3; margin-top:20px; font-size:12px;">
⚠️ 仅供参考，不构成投资建议</p>
</body></html>"""


def main():
    """主函数"""
    if len(sys.argv) < 2:
        print_banner()
        print("用法:")
        print("  python main.py screen      盘前选股")
        print("  python main.py monitor     盘中检查")
        print("  python main.py watch       持续监控")
        print("  python main.py backtest    回测策略")
        print("  python main.py analyze     复盘分析")
        print("  python main.py full        全流程 (选股→回测→复盘)")
        return

    cmd = sys.argv[1]

    commands = {
        "screen": cmd_screen,
        "monitor": cmd_monitor,
        "watch": lambda: cmd_watch(),
        "backtest": cmd_backtest,
        "analyze": cmd_analyze,
        "full": cmd_full,
    }

    if cmd in commands:
        commands[cmd]()
    else:
        print(f"未知命令: {cmd}")
        print("可用命令: screen, monitor, watch, backtest, analyze, full")


if __name__ == "__main__":
    main()
