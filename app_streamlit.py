"""
A股短线交易系统 - Streamlit Web 仪表盘
=========================================
启动: streamlit run app_streamlit.py
访问: http://localhost:8501
"""

import os
import sys
import time
import traceback
from datetime import datetime, timedelta
import warnings
warnings.filterwarnings("ignore")

import pandas as pd
import numpy as np
import streamlit as st

# 设置页面配置 - 必须放在所有st命令的最前面
st.set_page_config(
    page_title="A股短线交易系统",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

# 导入本地模块
from config import SCREEN, INDICATORS, STRATEGY, RISK, OUTPUT
from data_feed import DataFeed
from screener import StockScreener
from strategy import StrategyEngine, SignalType
from monitor import TradeManager, Monitor, Position
from backtest import BacktestEngine
from analysis import Analyzer


# ─── 初始化 ───

@st.cache_resource
def init_datafeed():
    return DataFeed()

@st.cache_resource
def init_strategy():
    return StrategyEngine()

@st.cache_resource
def init_analyzer():
    return Analyzer()

df = init_datafeed()
strategy_engine = init_strategy()
analyzer = init_analyzer()


# ─── 辅助函数 ───

def color_change(val):
    """涨幅颜色"""
    if val is None or pd.isna(val):
        return ""
    try:
        v = float(val)
        if v > 0:
            return "color: #ff5252; font-weight: 600;"
        elif v < 0:
            return "color: #00c853; font-weight: 600;"
    except (ValueError, TypeError):
        pass
    return ""

def fmt_pct(val):
    """格式化百分比"""
    if val is None or pd.isna(val):
        return "-"
    return f"{val:+.2f}%"

def fmt(val, decimals=2):
    """格式化数字"""
    if val is None or pd.isna(val):
        return "-"
    return f"{val:.{decimals}f}"


# ─── 侧边栏 ───

st.sidebar.markdown("""
<style>
    .sidebar-title { font-size: 22px; font-weight: 700; color: #2196f3; margin-bottom: 4px; }
    .sidebar-sub { font-size: 12px; color: #8a9aa8; margin-bottom: 20px; }
</style>
<div class="sidebar-title">📊 A股短线交易系统</div>
<div class="sidebar-sub">盘前选股 · 盘中监控 · 盘后复盘</div>
""", unsafe_allow_html=True)

page = st.sidebar.radio(
    "导航菜单",
    ["📊 市场概览", "🎯 选股", "📈 K线分析", "📋 回测", "📝 复盘报告"],
    index=0,
)

st.sidebar.markdown("---")
st.sidebar.markdown(f"**🕐 {datetime.now().strftime('%Y-%m-%d %H:%M')}**")
st.sidebar.markdown(
    '<p style="font-size:11px; color:#636e72; margin-top:30px;">'
    '⚠️ 仅供学习参考，不构成投资建议<br>'
    '数据来源: 东方财富/新浪财经</p>',
    unsafe_allow_html=True
)

# 恒速刷新选项
auto_refresh = st.sidebar.checkbox("自动刷新（60秒）", value=False)
refresh_placeholder = st.sidebar.empty()


# ─── 页面1: 市场概览 ───

if page == "📊 市场概览":
    st.title("📊 A股市场概览")
    st.caption(f"数据更新时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    with st.spinner("获取市场数据..."):
        try:
            stocks = df.get_stock_list()
            if stocks.empty:
                st.error("获取数据失败，请检查网络连接")
                st.stop()

            # ── 统计卡片 ──
            total = len(stocks)
            up_count = int((stocks["change_pct"] > 0).sum())
            down_count = int((stocks["change_pct"] < 0).sum())
            limit_up = int((stocks["change_pct"] >= 9.8).sum())
            limit_down = int((stocks["change_pct"] <= -9.8).sum())
            avg_change = stocks["change_pct"].mean()

            col1, col2, col3, col4, col5, col6 = st.columns(6)
            with col1:
                st.metric("股票总数", total)
            with col2:
                st.metric("上涨", up_count, delta=f"{up_count/total*100:.1f}%", delta_color="off")
            with col3:
                st.metric("下跌", down_count, delta=f"{down_count/total*100:.1f}%", delta_color="off")
            with col4:
                st.metric("涨停", limit_up)
            with col5:
                st.metric("跌停", limit_down)
            with col6:
                st.metric("平均涨幅", f"{avg_change:+.2f}%")

            # ── 涨跌分布 ──
            col1, col2 = st.columns(2)

            with col1:
                st.subheader("🏆 涨幅榜 TOP20")
                top_gainers = stocks.nlargest(20, "change_pct")[
                    ["code", "name", "price", "change_pct", "turnover_rate", "market_cap"]
                ].copy()
                top_gainers["涨幅"] = top_gainers["change_pct"].apply(fmt_pct)
                top_gainers["价格"] = top_gainers["price"].apply(lambda x: fmt(x))
                top_gainers["市值"] = top_gainers["market_cap"].apply(lambda x: f"{x:.0f}亿")
                top_gainers["换手率"] = top_gainers["turnover_rate"].apply(lambda x: f"{x:.1f}%")
                display = top_gainers[["code", "name", "价格", "涨幅", "换手率", "市值"]]
                display.index = range(1, len(display) + 1)
                st.dataframe(
                    display.style.applymap(color_change, subset=["涨幅"]),
                    use_container_width=True, height=480,
                    column_config={"涨幅": st.column_config.TextColumn("涨幅")}
                )

            with col2:
                st.subheader("📉 跌幅榜 TOP20")
                top_losers = stocks.nsmallest(20, "change_pct")[
                    ["code", "name", "price", "change_pct", "turnover_rate", "market_cap"]
                ].copy()
                top_losers["涨幅"] = top_losers["change_pct"].apply(fmt_pct)
                top_losers["价格"] = top_losers["price"].apply(lambda x: fmt(x))
                top_losers["市值"] = top_losers["market_cap"].apply(lambda x: f"{x:.0f}亿")
                top_losers["换手率"] = top_losers["turnover_rate"].apply(lambda x: f"{x:.1f}%")
                display = top_losers[["code", "name", "价格", "涨幅", "换手率", "市值"]]
                display.index = range(1, len(display) + 1)
                st.dataframe(
                    display.style.applymap(color_change, subset=["涨幅"]),
                    use_container_width=True, height=480
                )

            # ── 板块热度 ──
            with st.expander("🔥 概念板块热度", expanded=False):
                with st.spinner("获取板块数据..."):
                    boards = df.get_concept_board()
                    if not boards.empty:
                        boards = boards.sort_values("change_pct", ascending=False).head(30)
                        boards["涨幅"] = boards["change_pct"].apply(fmt_pct)
                        boards["涨停数"] = boards["rise_count"]
                        st.dataframe(
                            boards[["name", "涨幅", "涨停数"]].style.applymap(
                                color_change, subset=["涨幅"]
                            ),
                            use_container_width=True,
                            column_config={
                                "name": st.column_config.TextColumn("板块名称"),
                                "涨幅": st.column_config.TextColumn("涨幅"),
                                "涨停数": st.column_config.NumberColumn("涨停数"),
                            }
                        )

        except Exception as e:
            st.error(f"数据加载失败: {str(e)}")
            st.code(traceback.format_exc())


# ─── 页面2: 选股 ───

elif page == "🎯 选股":
    st.title("🎯 全市场选股")
    st.caption("基于多因子评分系统筛选短线标的")

    col1, col2 = st.columns([3, 1])
    with col2:
        run_btn = st.button("🚀 执行选股", type="primary", use_container_width=True)
    with col1:
        st.info(
            f"基础过滤: 市值{SCREEN['market_cap_min']}亿~{SCREEN['market_cap_max']}亿 "
            f"| 价格{SCREEN['price_min']}~{SCREEN['price_max']}元 "
            f"| 换手率{SCREEN['turnover_min']}%~{SCREEN['turnover_max']}% "
            f"| 自动排除ST"
        )

    # 可调参数
    with st.expander("⚙️ 选参调整"):
        col1, col2, col3 = st.columns(3)
        with col1:
            min_mc = st.number_input("最小市值(亿)", value=SCREEN["market_cap_min"])
            max_mc = st.number_input("最大市值(亿)", value=SCREEN["market_cap_max"])
            min_price = st.number_input("最低价格", value=SCREEN["price_min"])
        with col2:
            min_vol = st.number_input("最小成交额(亿)", value=SCREEN["avg_amount_min"])
            min_tr = st.number_input("最低换手率%", value=SCREEN["turnover_min"])
            max_tr = st.number_input("最高换手率%", value=SCREEN["turnover_max"])
        with col3:
            score_th = st.slider("评分阈值", 0, 100, STRATEGY["score_threshold"])
            max_stocks = st.slider("最大输出数量", 5, 50, STRATEGY["max_stocks"])

    if run_btn or "screen_result" in st.session_state:
        if run_btn:
            with st.spinner("正在全市场分析，请稍候..."):
                screener = StockScreener()
                result = screener.screen()
                st.session_state["screen_result"] = result
        else:
            result = st.session_state["screen_result"]

        if result.empty:
            st.warning("当前无符合条件标的")
        else:
            st.success(f"共选出 {len(result)} 只标的")

            # 评分分布图
            col1, col2 = st.columns([2, 1])
            with col2:
                score_cols = ["放量突破", "均线金叉", "MACD", "KDJ", "量价配合"]
                avg_scores = {c: result[c].mean() for c in score_cols if c in result.columns}
                scores_df = pd.DataFrame([avg_scores]).T
                scores_df.columns = ["平均分"]
                st.bar_chart(scores_df, height=200)

            with col1:
                st.subheader(f"🏆 推荐标的 TOP {len(result)}")
                # 显示表格
                display = result[["code", "name", "price", "change_pct",
                                  "market_cap", "turnover_rate",
                                  "综合评分"]].copy()
                display["涨幅"] = display["change_pct"].apply(fmt_pct)
                display["价格"] = display["price"].apply(lambda x: fmt(x))
                display["市值"] = display["market_cap"].apply(lambda x: f"{x:.0f}亿")
                display["换手率"] = display["turnover_rate"].apply(lambda x: f"{x:.1f}%")
                display["评分"] = display["综合评分"].apply(lambda x: f"{x:.0f}")

                # 添加详细评分列
                for c in score_cols:
                    if c in result.columns:
                        display[c] = result[c].apply(lambda x: f"{x:.0f}")

                cols = ["code", "name", "价格", "涨幅", "市值", "换手率", "评分"]
                for c in score_cols:
                    if c in display.columns:
                        cols.append(c)

                st.dataframe(
                    display[cols].style.applymap(color_change, subset=["涨幅"]),
                    use_container_width=True,
                    column_config={
                        "code": st.column_config.TextColumn("代码"),
                        "name": st.column_config.TextColumn("名称"),
                        "涨幅": st.column_config.TextColumn("涨幅"),
                    }
                )

            # 导出
            csv = result.to_csv(index=False, encoding="utf-8-sig")
            st.download_button(
                "📥 导出CSV", data=csv, file_name="选股结果.csv",
                mime="text/csv", use_container_width=True
            )
    else:
        st.info("👆 点击「执行选股」开始全市场分析")


# ─── 页面3: K线分析 ───

elif page == "📈 K线分析":
    st.title("📈 K线技术分析")

    col1, col2 = st.columns([1, 3])
    with col1:
        code = st.text_input("股票代码", value="600519").strip()
        period = st.selectbox("周期", ["day", "week", "month"], index=0)
        count = st.slider("K线数量", 30, 250, 120)
        analyze_btn = st.button("分析", type="primary", use_container_width=True)

        # 指标切换
        st.markdown("**技术指标**")
        show_ma = st.checkbox("均线 MA5/10/20/60", value=True)
        show_macd = st.checkbox("MACD", value=True)
        show_rsi = st.checkbox("RSI", value=False)
        show_kdj = st.checkbox("KDJ", value=False)
        show_boll = st.checkbox("布林带 BOLL", value=False)

    with col2:
        if analyze_btn or "last_code" in st.session_state:
            if analyze_btn:
                st.session_state["last_code"] = code

            code = st.session_state["last_code"]

            with st.spinner(f"获取 {code} 数据..."):
                try:
                    kline = df.get_kline(code, period=period, count=count)
                    if kline.empty:
                        st.error("获取K线数据失败")
                        st.stop()

                    # 获取股票信息
                    stocks = df.get_stock_list()
                    stock_name = ""
                    if not stocks.empty:
                        match = stocks[stocks["code"] == code]
                        if not match.empty:
                            s = match.iloc[0]
                            stock_name = s.get("name", "")

                    latest = kline.iloc[-1]
                    pre = kline.iloc[-2] if len(kline) > 1 else latest

                    # 股票信息
                    info_cols = st.columns(6)
                    info_cols[0].metric("代码", code)
                    info_cols[1].metric("名称", stock_name or "未知")
                    info_cols[2].metric(
                        "收盘价", f"¥{latest['close']:.2f}",
                        delta=f"{latest.get('change_pct', 0):+.2f}%"
                    )
                    info_cols[3].metric(
                        "MA5", f"{latest.get('MA5', 0):.2f}" if not pd.isna(latest.get('MA5', np.nan)) else "-"
                    )
                    info_cols[4].metric(
                        "MA20", f"{latest.get('MA20', 0):.2f}" if not pd.isna(latest.get('MA20', np.nan)) else "-"
                    )
                    info_cols[5].metric(
                        "成交量", f"{latest['volume']/1e4:.0f}万手"
                    )

                    # 买卖信号
                    sig = strategy_engine.generate_buy_signals(
                        kline, {"code": code, "name": stock_name, "price": latest["close"]}
                    )
                    if sig:
                        st.markdown(
                            f'<div style="background:rgba(0,200,83,0.1); border-left:4px solid #00c853; '
                            f'padding:12px 16px; border-radius:4px; margin:12px 0;">'
                            f'<b>🟢 买入信号:</b> {sig.reason} '
                            f'| 可信度: {sig.score}/100 '
                            f'| 止损: ¥{sig.stop_loss:.2f} '
                            f'| 止盈: ¥{sig.take_profit:.2f}'
                            f'</div>', unsafe_allow_html=True
                        )

                    # ── K线图 ──
                    try:
                        import plotly.graph_objects as go
                        from plotly.subplots import make_subplots

                        # 判断是否显示副图指标
                        has_secondary = show_macd or show_rsi or show_kdj
                        rows = 3 if has_secondary else 2
                        row_heights = [0.5, 0.15, 0.35] if has_secondary else [0.65, 0.35]
                        specs = [[{"secondary_y": True}],
                                 [{"secondary_y": False}]]

                        fig = make_subplots(
                            rows=rows, cols=1,
                            shared_xaxes=True,
                            vertical_spacing=0.05,
                            row_heights=row_heights,
                            specs=specs if not has_secondary else
                                  [[{"secondary_y": True}], [{"secondary_y": False}], [{"secondary_y": False}]]
                        )

                        # 主图: K线
                        colors = ["#ff5252" if c >= o else "#00c853"
                                 for c, o in zip(kline["close"], kline["open"])]
                        fig.add_trace(go.Candlestick(
                            x=kline["date"], open=kline["open"], high=kline["high"],
                            low=kline["low"], close=kline["close"],
                            increasing_line_color="#ff5252", decreasing_line_color="#00c853",
                            showlegend=False, name="K线"
                        ), row=1, col=1)

                        # 均线
                        if show_ma:
                            for ma, color, width in [("MA5", "#ff9800", 1), ("MA10", "#00c853", 1),
                                                      ("MA20", "#e040fb", 1), ("MA60", "#536dfe", 1)]:
                                if ma in kline.columns:
                                    valid = kline[ma].notna()
                                    if valid.any():
                                        fig.add_trace(go.Scatter(
                                            x=kline["date"][valid], y=kline[ma][valid],
                                            mode="lines", name=ma, line=dict(color=color, width=width)
                                        ), row=1, col=1)

                        # 布林带
                        if show_boll and "BOLL_UP" in kline.columns:
                            valid = kline["BOLL_MID"].notna()
                            if valid.any():
                                fig.add_trace(go.Scatter(
                                    x=kline["date"][valid], y=kline["BOLL_UP"][valid],
                                    mode="lines", name="BOLL上轨",
                                    line=dict(color="#e040fb", width=0.8, dash="dash")
                                ), row=1, col=1)
                                fig.add_trace(go.Scatter(
                                    x=kline["date"][valid], y=kline["BOLL_MID"][valid],
                                    mode="lines", name="BOLL中轨",
                                    line=dict(color="#e040fb", width=0.8)
                                ), row=1, col=1)
                                fig.add_trace(go.Scatter(
                                    x=kline["date"][valid], y=kline["BOLL_DN"][valid],
                                    mode="lines", name="BOLL下轨",
                                    line=dict(color="#e040fb", width=0.8, dash="dash")
                                ), row=1, col=1)

                        # 成交量
                        vol_colors = ["#ff5252" if c >= o else "#00c853"
                                     for c, o in zip(kline["close"], kline["open"])]
                        fig.add_trace(go.Bar(
                            x=kline["date"], y=kline["volume"]/1e4,
                            marker_color=vol_colors, opacity=0.5,
                            name="成交量(万手)", showlegend=False,
                        ), row=2, col=1)

                        # MACD
                        if show_macd and "MACD_DIF" in kline.columns:
                            valid = kline["MACD_DIF"].notna()
                            if valid.any():
                                bar_colors = ["#ff5252" if v >= 0 else "#00c853"
                                             for v in kline["MACD_BAR"].fillna(0)]
                                fig.add_trace(go.Bar(
                                    x=kline["date"], y=kline["MACD_BAR"],
                                    marker_color=bar_colors, opacity=0.5,
                                    name="MACD柱", showlegend=False,
                                ), row=3, col=1)
                                fig.add_trace(go.Scatter(
                                    x=kline["date"][valid], y=kline["MACD_DIF"][valid],
                                    mode="lines", name="DIF",
                                    line=dict(color="#ff9800", width=1.5)
                                ), row=3, col=1)
                                fig.add_trace(go.Scatter(
                                    x=kline["date"][valid], y=kline["MACD_DEA"][valid],
                                    mode="lines", name="DEA",
                                    line=dict(color="#e040fb", width=1.5)
                                ), row=3, col=1)

                        # RSI
                        if show_rsi and "RSI" in kline.columns:
                            valid = kline["RSI"].notna()
                            row_r = 3 if not show_macd else 3  # 复用MACD行
                            if valid.any():
                                fig.add_trace(go.Scatter(
                                    x=kline["date"][valid], y=kline["RSI"][valid],
                                    mode="lines", name="RSI",
                                    line=dict(color="#ff5252", width=1.5)
                                ), row=3, col=1)
                                fig.add_hline(y=70, line_dash="dash", line_color="#ff5252",
                                              opacity=0.3, row=3, col=1)
                                fig.add_hline(y=30, line_dash="dash", line_color="#00c853",
                                              opacity=0.3, row=3, col=1)

                        # KDJ
                        if show_kdj and "KDJ_K" in kline.columns:
                            valid = kline["KDJ_K"].notna()
                            row_k = 3 if not show_macd and not show_rsi else 3
                            if valid.any():
                                for k, color in [("KDJ_K", "#ff9800"), ("KDJ_D", "#e040fb"),
                                                  ("KDJ_J", "#536dfe")]:
                                    if k in kline.columns:
                                        fig.add_trace(go.Scatter(
                                            x=kline["date"][valid], y=kline[k][valid],
                                            mode="lines", name=k,
                                            line=dict(color=color, width=1)
                                        ), row=3, col=1)

                        # 布局
                        fig.update_layout(
                            xaxis_rangeslider_visible=False,
                            template="plotly_dark",
                            height=650,
                            margin=dict(l=40, r=20, t=10, b=40),
                            hovermode="x unified",
                            showlegend=True,
                            legend=dict(
                                orientation="h", yanchor="bottom", y=1.02,
                                xanchor="right", x=1, font=dict(size=10)
                            ),
                        )

                        # Y轴标签
                        fig.update_yaxes(title_text="价格", row=1, col=1)
                        fig.update_yaxes(title_text="成交量(万手)", row=2, col=1)
                        if has_secondary:
                            fig.update_yaxes(title_text="指标", row=3, col=1)

                        st.plotly_chart(fig, use_container_width=True)

                    except ImportError:
                        st.warning("安装 plotly 可查看交互式K线图: pip install plotly")
                        # 回退到matplotlib
                        st.line_chart(kline.set_index("date")["close"])

                    # 最近N日数据表
                    with st.expander("📋 最近数据", expanded=False):
                        tail_n = st.slider("显示天数", 5, 60, 20)
                        show_cols = ["date", "open", "close", "high", "low", "volume",
                                     "change_pct", "MA5", "MA10", "MA20",
                                     "MACD_DIF", "MACD_DEA", "RSI", "KDJ_K"]
                        display_cols = [c for c in show_cols if c in kline.columns]
                        tail = kline[display_cols].tail(tail_n).copy()
                        # 格式化
                        for c in tail.columns:
                            if c == "date":
                                tail[c] = tail[c].dt.strftime("%m-%d")
                            elif c == "volume":
                                tail[c] = tail[c].apply(lambda x: f"{x/1e4:.0f}")
                            elif c == "change_pct":
                                tail[c] = tail[c].apply(fmt_pct)
                            elif c not in ("date",):
                                tail[c] = tail[c].apply(lambda x: fmt(x) if not pd.isna(x) else "-")
                        st.dataframe(tail, use_container_width=True)

                except Exception as e:
                    st.error(f"分析失败: {str(e)}")
                    st.code(traceback.format_exc())
        else:
            st.info("👆 输入股票代码后点击「分析」")


# ─── 页面4: 回测 ───

elif page == "📋 回测":
    st.title("📋 策略回测")
    st.caption("历史K线回测，验证策略有效性")

    col1, col2 = st.columns([2, 1])
    with col1:
        codes_input = st.text_input(
            "股票代码（多个用逗号分隔）",
            value="600519,000858,300750,002415,601318",
            help="示例: 600519,000858,300750"
        )
    with col2:
        days = st.number_input("回测天数", value=120, min_value=30, max_value=800)

    run_btn = st.button("🚀 开始回测", type="primary", use_container_width=True)

    if run_btn:
        codes = [c.strip() for c in codes_input.split(",") if c.strip()]
        if not codes:
            st.error("请输入股票代码")
            st.stop()

        progress_bar = st.progress(0)
        status_text = st.empty()

        results = []
        bt = BacktestEngine()

        for i, code in enumerate(codes):
            status_text.text(f"回测 {i+1}/{len(codes)}: {code}")
            result = bt.run(code, "", days=days)
            if result:
                results.append(result)
            progress_bar.progress((i + 1) / len(codes))

        progress_bar.empty()
        status_text.empty()

        if not results:
            st.error("回测无结果")
        else:
            summary = pd.DataFrame([{
                "代码": r["code"],
                "名称": r.get("name", ""),
                "总收益率%": r["total_return"],
                "年化收益率%": r["annual_return"],
                "最大回撤%": r["max_drawdown"],
                "胜率%": r["win_rate"],
                "夏普比率": r["sharpe_ratio"],
                "交易次数": r["trade_count"],
                "盈亏比": r["profit_loss_ratio"],
            } for r in results])

            summary = summary.sort_values("总收益率%", ascending=False)

            st.success(f"回测完成 ({len(results)}只)")

            # 着色
            def highlight_pnl(val):
                try:
                    v = float(str(val).replace("%", ""))
                    if v > 0:
                        return "color: #ff5252;"
                    elif v < 0:
                        return "color: #00c853;"
                except:
                    pass
                return ""

            st.dataframe(
                summary.style.applymap(highlight_pnl, subset=["总收益率%", "年化收益率%"]),
                use_container_width=True,
                hide_index=True,
            )

            # 详细交易记录
            for result in results[:5]:  # 最多展示5只
                trades = result.get("trades", [])
                if trades:
                    with st.expander(f"📋 {result.get('name', '')}({result['code']}) "
                                     f"- {len(trades)}笔交易"):
                        trades_df = pd.DataFrame(trades)
                        if "盈亏%" in trades_df.columns:
                            trades_df["盈亏%"] = trades_df["盈亏%"].apply(
                                lambda x: f"{x:+.2f}%" if pd.notna(x) else "-"
                            )
                        st.dataframe(trades_df, use_container_width=True, hide_index=True)

            # 导出
            csv = summary.to_csv(index=False, encoding="utf-8-sig")
            st.download_button(
                "📥 导出回测结果", data=csv, file_name="回测汇总.csv",
                mime="text/csv", use_container_width=True
            )
    else:
        st.info("👆 输入股票代码后点击「开始回测」")

        # 默认展示
        with st.expander("📖 使用说明"):
            st.markdown("""
            **回测规则：**
            - 初始资金: 10万元
            - 佣金: 万2.5 | 印花税: 千1 | 滑点: 0.1%
            - 止损: -3% | 止盈: +6%
            - 单票仓位: 20%

            **策略信号：**
            - 放量突破(MA20/MA60)
            - MA5金叉MA10
            - MACD零轴金叉
            - KDJ低位金叉

            **绩效指标：**
            - 总收益率、年化收益率、最大回撤
            - 胜率、夏普比率、盈亏比
            """)


# ─── 页面5: 复盘报告 ───

elif page == "📝 复盘报告":
    st.title("📝 交易复盘报告")
    st.caption("导入交易记录进行绩效分析")

    tab1, tab2 = st.tabs(["📤 导入数据", "📊 分析报告"])

    with tab1:
        st.markdown("### 导入交易记录")
        st.markdown("上传CSV文件或手动输入交易记录")

        upload_method = st.radio("导入方式", ["上传CSV", "手动输入"], horizontal=True)

        if upload_method == "上传CSV":
            uploaded_file = st.file_uploader(
                "选择CSV文件", type=["csv"],
                help="CSV需包含: 卖出日期,名称,买入价,卖出价,盈亏%等列"
            )
            if uploaded_file:
                try:
                    df_trades = pd.read_csv(uploaded_file)
                    st.success(f"已加载 {len(df_trades)} 条记录")
                    st.dataframe(df_trades, use_container_width=True)
                    st.session_state["trades_df"] = df_trades
                except Exception as e:
                    st.error(f"读取失败: {e}")

        else:
            st.markdown("**手动输入交易（一行一笔）**")
            sample = ("卖出日期,名称,买入价,卖出价,盈亏%,持仓天数,策略\n"
                      "2026-01-15,贵州茅台,1500,1580,+5.33,5,放量突破\n"
                      "2026-01-20,五粮液,130,125,-3.85,3,MACD金叉")
            csv_text = st.text_area("粘贴CSV文本", value=sample, height=180)
            if st.button("解析", type="primary"):
                try:
                    from io import StringIO
                    df_trades = pd.read_csv(StringIO(csv_text))
                    if not df_trades.empty:
                        st.success(f"已解析 {len(df_trades)} 条记录")
                        st.dataframe(df_trades, use_container_width=True)
                        st.session_state["trades_df"] = df_trades
                    else:
                        st.error("无有效数据")
                except Exception as e:
                    st.error(f"解析失败: {e}")

    with tab2:
        if "trades_df" in st.session_state and not st.session_state["trades_df"].empty:
            df_trades = st.session_state["trades_df"]
            trades = df_trades.to_dict("records")

            with st.spinner("分析中..."):
                analysis = analyzer.analyze_trades(trades)

            if "error" in analysis:
                st.error(analysis["error"])
            else:
                # 核心指标
                st.subheader("📈 绩效概览")
                col1, col2, col3, col4, col5 = st.columns(5)
                metrics_data = [
                    ("总交易次数", analysis["总交易次数"], ""),
                    ("胜率", f"{analysis['胜率']}%",
                     "normal" if analysis["胜率"] >= 50 else "inverse"),
                    ("总盈亏", f"{analysis['总盈亏%']:+.2f}%",
                     "off" if analysis["总盈亏%"] >= 0 else "inverse"),
                    ("盈亏比", f"{analysis['盈亏比']:.2f}", ""),
                    ("最大回撤", f"{analysis['最大回撤%']:.2f}%", "inverse"),
                ]
                for i, (label, val, delta) in enumerate(metrics_data):
                    [col1, col2, col3, col4, col5][i].metric(label, val, delta=delta, delta_color="off")

                col1, col2, col3, col4, col5 = st.columns(5)
                extra_metrics = [
                    ("盈利次数", analysis["盈利次数"], ""),
                    ("亏损次数", analysis["亏损次数"], ""),
                    ("平均盈利", f"{analysis['平均盈利%']:+.2f}%", ""),
                    ("平均亏损", f"{analysis['平均亏损%']:+.2f}%", ""),
                    ("平均持仓", f"{analysis['平均持仓天数']}天", ""),
                ]
                for i, (label, val, delta) in enumerate(extra_metrics):
                    [col1, col2, col3, col4, col5][i].metric(label, val)

                # 月度统计
                monthly = analysis.get("月度统计", pd.DataFrame())
                if not monthly.empty:
                    st.subheader("📅 月度表现")
                    st.dataframe(monthly, use_container_width=True)

                # 策略分析
                strategy_stats = analysis.get("策略分析", {})
                if strategy_stats:
                    st.subheader("🎯 策略表现")
                    strategy_df = pd.DataFrame(strategy_stats).T
                    strategy_df.index.name = "策略"
                    st.dataframe(strategy_df, use_container_width=True)

                # 交易明细
                trades_df = analysis.get("交易明细", pd.DataFrame())
                if not trades_df.empty:
                    st.subheader("📋 交易明细")
                    st.dataframe(trades_df, use_container_width=True)

                # 导出报告
                report_html = analyzer.generate_report(trades, output_file="复盘报告.html")
                with open(report_html, "r", encoding="utf-8") as f:
                    html_data = f.read()
                st.download_button(
                    "📥 下载HTML报告", data=html_data,
                    file_name="复盘报告.html", mime="text/html",
                    use_container_width=True
                )
        else:
            st.info("👆 请先在「导入数据」标签页导入交易记录")


# ─── 自动刷新 ───

if auto_refresh:
    refresh_placeholder.info(f"⏳ 自动刷新中... 下次刷新: 60秒后")
    time.sleep(1)
    st.rerun()
