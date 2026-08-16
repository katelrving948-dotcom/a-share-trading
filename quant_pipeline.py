"""End-to-end factor research, walk-forward backtest, report and email runner."""

from __future__ import annotations

import argparse
import json
import logging
import os
from datetime import date, datetime, timedelta
from email.message import EmailMessage
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from email_digest import send_email
from quant_backtest import BacktestCosts
from quant_data import QuantDailyData, QuantDataConfig
from quant_journal import (
    append_optimization_log,
    build_optimization_entry,
    validate_previous_signals,
)
from quant_optimizer import OptimizationConfig, walk_forward_optimize


LOGGER = logging.getLogger("quant_pipeline")
SHANGHAI = ZoneInfo("Asia/Shanghai")


def _json_default(value):
    if isinstance(value, (pd.Timestamp, date)):
        return value.isoformat()
    if hasattr(value, "item"):
        return value.item()
    raise TypeError(f"无法序列化 {type(value).__name__}")


def _metrics_table(metrics: dict) -> str:
    labels = {
        "total_return": "累计收益(%)",
        "annual_return": "年化收益(%)",
        "max_drawdown": "最大回撤(%)",
        "sharpe_ratio": "夏普比率",
        "trading_days": "交易日数",
    }
    rows = "".join(
        f"<tr><td>{labels[key]}</td><td>{metrics.get(key, '--')}</td></tr>"
        for key in labels
    )
    return f"<table border='1' cellspacing='0' cellpadding='6'>{rows}</table>"


def _build_html_report(result: dict, metadata: dict, optimization_entry: dict | None = None) -> str:
    signals = result["full_backtest"]["signals"].copy()
    if "factor_score" in signals:
        signals["factor_score"] = signals["factor_score"].round(4)
    folds = pd.DataFrame([
        {
            "训练区间": f"{fold['train_start']}~{fold['train_end']}",
            "验证区间": f"{fold['validation_start']}~{fold['validation_end']}",
            "训练夏普": fold["train_metrics"].get("sharpe_ratio"),
            "样本外夏普": fold["validation_metrics"].get("sharpe_ratio"),
            "样本外年化%": fold["validation_metrics"].get("annual_return"),
            "样本外最大回撤%": fold["validation_metrics"].get("max_drawdown"),
        }
        for fold in result["folds"]
    ])
    optimization_entry = optimization_entry or {}
    validation = optimization_entry.get("validation") or {}
    changes = optimization_entry.get("parameter_changes") or []
    optimization_html = "".join(
        f"<li>{item['part']}：{item.get('before')} → {item.get('after')}</li>"
        for item in changes
    ) or "<li>参数窗口保持不变，避免根据单日结果过度调参</li>"
    validation_html = (
        f"{validation.get('signal_date')} → {validation.get('validation_date')}："
        f"命中率{validation.get('hit_rate')}%，平均收益{validation.get('average_return')}%，"
        f"相对全市场等权超额{validation.get('excess_return')}%"
        if validation.get("status") == "validated" else validation.get("message", "尚无次日验证")
    )
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<title>A股量化因子滚动优化报告</title><style>
body{{font-family:Arial,'Microsoft YaHei',sans-serif;max-width:1100px;margin:30px auto;color:#172033}}
table{{border-collapse:collapse;width:100%;margin:12px 0}}th,td{{border:1px solid #dbe2ea;padding:7px;text-align:right}}
th{{background:#eff6ff}}td:first-child,th:first-child{{text-align:left}}.risk{{background:#fff7ed;padding:12px;border-left:4px solid #f59e0b}}
</style></head><body><h1>A股量化因子分析与滚动优化</h1>
<p>生成时间：{metadata['generated_at']}；数据源：{metadata['provider']}；覆盖股票：{metadata['stock_count']}只；交易日：{metadata['trading_days']}。</p>
<div class="risk">该通道用于检验因子稳定性，不代表提高了交易结果的确定性。信号在收盘后生成，下一交易日执行；需警惕幸存者偏差、停牌/涨跌停不可成交、数据复权变化及参数过拟合。</div>
<h2>最新最优参数</h2><pre>{json.dumps(result['best_params'], ensure_ascii=False, indent=2)}</pre>
<h2>汇总样本外表现</h2>{_metrics_table(result['oos_metrics'])}
<h2>今日验证与优化日志</h2><p>{validation_html}</p><ul>{optimization_html}</ul>
<p>{optimization_entry.get('guardrail', '')}</p>
<h2>滚动折次</h2>{folds.to_html(index=False, border=0) if not folds.empty else '<p>无有效折次</p>'}
<h2>当日收盘信号（下一交易日观察）</h2>{signals.to_html(index=False, border=0) if not signals.empty else '<p>无有效信号</p>'}
<h2>成本口径</h2><p>买卖佣金万三；卖出印花税千一；买卖双边滑点0.1%。前20只按目标权重等权，按日调仓。</p>
</body></html>"""


def _save_outputs(
    result: dict,
    metadata: dict,
    output_dir: Path,
    previous_summary: dict | None = None,
    validation: dict | None = None,
) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    previous_summary = previous_summary or {}
    validation = validation or {"status": "missing", "message": "没有上一期信号可验证"}
    optimization_entry = build_optimization_entry(
        metadata["generated_at"], previous_summary, result, validation
    )
    files = {
        "report": output_dir / "quant_report.html",
        "summary": output_dir / "quant_summary.json",
        "signals": output_dir / "quant_signals.csv",
        "equity": output_dir / "quant_oos_equity.csv",
        "folds": output_dir / "quant_folds.csv",
        "factors": output_dir / "quant_factors_latest.csv",
        "optimization_log": output_dir / "quant_optimization_log.json",
    }
    files["report"].write_text(
        _build_html_report(result, metadata, optimization_entry), encoding="utf-8"
    )
    summary = {
        "metadata": metadata,
        "best_params": result["best_params"],
        "oos_metrics": result["oos_metrics"],
        "folds": result["folds"],
        "costs": result["full_backtest"].get("costs", {}),
        "latest_validation": validation,
        "optimization_log_entry": optimization_entry,
    }
    files["summary"].write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=_json_default),
        encoding="utf-8",
    )
    result["full_backtest"]["signals"].to_csv(files["signals"], index=False, encoding="utf-8-sig")
    result["oos_equity"].to_csv(files["equity"], index=False, encoding="utf-8-sig")
    pd.json_normalize(result["folds"]).to_csv(files["folds"], index=False, encoding="utf-8-sig")
    factors = result["factors"]
    latest_date = factors["date"].max()
    factors[factors["date"] == latest_date].to_csv(files["factors"], index=False, encoding="utf-8-sig")
    append_optimization_log(files["optimization_log"], optimization_entry)
    return {name: str(path.resolve()) for name, path in files.items()}


def _send_report_email(result: dict, metadata: dict) -> None:
    metrics = result["oos_metrics"]
    signals = result["full_backtest"]["signals"]
    message = EmailMessage()
    message["Subject"] = f"{metadata['signal_date']} A股量化因子样本外报告"
    plain_signals = "\n".join(
        f"{row.rank}. {row.code} {getattr(row, 'name', '')} 因子分{row.factor_score:.4f}"
        for row in signals.itertuples(index=False)
    ) or "无有效信号"
    message.set_content(
        "A股量化因子滚动优化报告\n"
        f"最优参数：{json.dumps(result['best_params'], ensure_ascii=False)}\n"
        f"样本外年化：{metrics.get('annual_return', 0)}%\n"
        f"样本外最大回撤：{metrics.get('max_drawdown', 0)}%\n"
        f"样本外夏普：{metrics.get('sharpe_ratio', 0)}\n\n"
        "下一交易日观察信号：\n" + plain_signals
        + "\n\n仅供量化研究，不构成投资建议；样本外结果不保证未来表现。"
    )
    message.add_alternative(_build_html_report(result, metadata), subtype="html")
    send_email(message)


def run_pipeline(send_mail: bool = False) -> dict:
    run_date = datetime.now(SHANGHAI).date()
    end = run_date
    while end.weekday() >= 5:
        end -= timedelta(days=1)
    history_years = int(os.getenv("QUANT_HISTORY_YEARS", "4"))
    start = date(end.year - history_years, end.month, min(end.day, 28))
    symbols = [item.strip() for item in os.getenv("QUANT_SYMBOLS", "").split(",") if item.strip()]
    data_config = QuantDataConfig(
        provider=os.getenv("QUANT_DATA_PROVIDER", "auto"),
        universe_limit=int(os.getenv("QUANT_UNIVERSE_LIMIT", "500")),
        include_bj=os.getenv("QUANT_INCLUDE_BJ", "0") == "1",
        max_workers=int(os.getenv("QUANT_MAX_WORKERS", "6")),
        cache_dir=os.getenv("QUANT_CACHE_DIR", ".cache/quant_daily"),
    )
    provider = QuantDailyData(data_config)
    prices = provider.fetch(start=start, end=end, symbols=symbols or None)
    stock_count = int(prices["code"].nunique())
    trading_days = int(prices["date"].nunique())
    LOGGER.info("开始滚动优化：%s只股票，%s个交易日", stock_count, trading_days)
    output_dir = Path(os.getenv("QUANT_OUTPUT_DIR", "output/quant"))
    summary_path = output_dir / "quant_summary.json"
    signals_path = output_dir / "quant_signals.csv"
    try:
        previous_summary = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.exists() else {}
    except (OSError, json.JSONDecodeError):
        previous_summary = {}
    try:
        previous_signals = pd.read_csv(signals_path, dtype={"code": str}) if signals_path.exists() else pd.DataFrame()
    except (OSError, pd.errors.ParserError, pd.errors.EmptyDataError):
        previous_signals = pd.DataFrame()
    validation = validate_previous_signals(previous_signals, prices)
    result = walk_forward_optimize(
        prices,
        OptimizationConfig(top_n=int(os.getenv("QUANT_TOP_N", "20"))),
        BacktestCosts(commission=0.0003, stamp_tax=0.001, slippage=0.001),
    )
    signal_date = (
        result["full_backtest"]["signals"]["date"].max().strftime("%Y-%m-%d")
        if not result["full_backtest"]["signals"].empty else "无信号日期"
    )
    metadata = {
        "generated_at": pd.Timestamp.now(tz="Asia/Shanghai").strftime("%Y-%m-%d %H:%M:%S"),
        "signal_date": signal_date,
        "provider": provider.provider_name,
        "stock_count": stock_count,
        "trading_days": trading_days,
        "universe_limit": data_config.universe_limit,
        "current_trading_day_confirmed": signal_date == run_date.strftime("%Y-%m-%d"),
    }
    files = _save_outputs(
        result, metadata,
        output_dir,
        previous_summary=previous_summary,
        validation=validation,
    )
    if send_mail and metadata["current_trading_day_confirmed"]:
        _send_report_email(result, metadata)
    elif send_mail:
        LOGGER.warning("最新信号日期为%s，不是%s；跳过重复/节假日邮件", signal_date, run_date)
    LOGGER.info("量化流程完成，报告：%s", files["report"])
    return {"metadata": metadata, "files": files, "oos_metrics": result["oos_metrics"]}


def main() -> None:
    parser = argparse.ArgumentParser(description="A股量化因子滚动优化")
    parser.add_argument("--send-email", action="store_true", help="完成后发送独立量化报告邮件")
    args = parser.parse_args()
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )
    summary = run_pipeline(send_mail=args.send_email)
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=_json_default))


if __name__ == "__main__":
    main()
