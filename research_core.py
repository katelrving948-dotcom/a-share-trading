"""Shared snapshots consumed by the three-page site and the noon email."""

from __future__ import annotations

import json
import math
import os
import shutil
import tempfile
import threading
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

import pandas as pd

from data_feed import DataFeed
from fundamental import FundamentalScorer


SHANGHAI = ZoneInfo("Asia/Shanghai")
RESEARCH_DIR = Path(os.getenv("RESEARCH_OUTPUT_DIR", "output/research"))
QUANT_DIR = Path(os.getenv("QUANT_OUTPUT_DIR", "output/quant"))
FUNDAMENTAL_FILE = RESEARCH_DIR / "fundamental_latest.json"
PUBLIC_SNAPSHOT_BASE = os.getenv(
    "SNAPSHOT_PUBLIC_BASE_URL",
    "https://raw.githubusercontent.com/katelrving948-dotcom/a-share-trading/main/output",
).rstrip("/")
_snapshot_sync_lock = threading.Lock()
_snapshot_sync_checked_at = 0.0


def _clean(value):
    if isinstance(value, dict):
        return {str(key): _clean(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_clean(item) for item in value]
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    if hasattr(value, "item"):
        value = value.item()
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    return value


def save_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_clean(payload), ensure_ascii=False, indent=2), encoding="utf-8")


def _generated_at(payload: dict) -> str:
    return str(payload.get("summary", payload).get("generated_at") or payload.get("metadata", {}).get("generated_at") or "")


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _download_public(relative_path: str) -> bytes:
    request = Request(
        f"{PUBLIC_SNAPSHOT_BASE}/{relative_path}",
        headers={"User-Agent": "a-share-research-hub", "Cache-Control": "no-cache"},
    )
    with urlopen(request, timeout=8) as response:
        return response.read()


def _replace_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        handle.write(content)
        temporary = Path(handle.name)
    temporary.replace(path)


def sync_public_snapshots(force: bool = False) -> None:
    """Refresh committed daily snapshots without requiring a Render redeploy."""
    global _snapshot_sync_checked_at
    if os.getenv("SNAPSHOT_REMOTE_ENABLED", "1") == "0":
        return
    interval = max(60, int(os.getenv("SNAPSHOT_SYNC_INTERVAL", "300")))
    with _snapshot_sync_lock:
        now = time.monotonic()
        if not force and now - _snapshot_sync_checked_at < interval:
            return
        _snapshot_sync_checked_at = now
        try:
            remote_fundamental_bytes = _download_public("research/fundamental_latest.json")
            remote_fundamental = json.loads(remote_fundamental_bytes.decode("utf-8"))
            if _generated_at(remote_fundamental) > _generated_at(_read_json(FUNDAMENTAL_FILE)):
                _replace_bytes(FUNDAMENTAL_FILE, remote_fundamental_bytes)
        except (OSError, ValueError, json.JSONDecodeError):
            pass

        try:
            remote_summary_bytes = _download_public("quant/quant_summary.json")
            remote_summary = json.loads(remote_summary_bytes.decode("utf-8"))
            summary_path = QUANT_DIR / "quant_summary.json"
            if _generated_at(remote_summary) > _generated_at(_read_json(summary_path)):
                factors = _download_public("quant/quant_factors_latest.csv")
                signals = _download_public("quant/quant_signals.csv")
                _replace_bytes(QUANT_DIR / "quant_factors_latest.csv", factors)
                _replace_bytes(QUANT_DIR / "quant_signals.csv", signals)
                _replace_bytes(summary_path, remote_summary_bytes)
        except (OSError, ValueError, json.JSONDecodeError):
            pass


def refresh_fundamental(universe_limit: int | None = None, progress_callback=None) -> dict:
    scorer = FundamentalScorer(DataFeed())
    rows = scorer.score(universe_limit=universe_limit, progress_callback=progress_callback)
    payload = {"summary": scorer.summary, "rows": rows}
    save_json(FUNDAMENTAL_FILE, payload)
    return _clean(payload)


def load_fundamental() -> dict:
    sync_public_snapshots()
    if not FUNDAMENTAL_FILE.exists():
        return {"summary": {"state": "missing", "message": "尚未生成基本面快照"}, "rows": []}
    try:
        return json.loads(FUNDAMENTAL_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"summary": {"state": "error", "message": f"基本面快照读取失败：{exc}"}, "rows": []}


def load_technical() -> dict:
    sync_public_snapshots()
    summary_path = QUANT_DIR / "quant_summary.json"
    factors_path = QUANT_DIR / "quant_factors_latest.csv"
    signals_path = QUANT_DIR / "quant_signals.csv"
    summary = {}
    if summary_path.exists():
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            summary = {"state": "error", "message": f"量化摘要读取失败：{exc}"}
    rows = []
    if factors_path.exists():
        frame = pd.read_csv(factors_path, dtype={"code": str})
        if "factor_score" in frame:
            frame = frame.sort_values("factor_score", ascending=False)
            frame["technical_score"] = (frame["factor_score"] * 100).round(2)
            frame["technical_rank"] = range(1, len(frame) + 1)
        rows = _clean(frame.to_dict("records"))
    signals = []
    if signals_path.exists():
        signals = _clean(pd.read_csv(signals_path, dtype={"code": str}).to_dict("records"))
    metadata = summary.get("metadata", {})
    if not rows and not summary:
        metadata = {"state": "missing", "message": "尚未生成量化快照，请等待16:30任务或手动运行"}
    return {
        "summary": {
            "metadata": metadata,
            "best_params": summary.get("best_params", {}),
            "oos_metrics": summary.get("oos_metrics", {}),
            "costs": summary.get("costs", {}),
            "factor_count": len(rows),
            "latest_validation": summary.get("latest_validation", {}),
            "optimization_log_entry": summary.get("optimization_log_entry", {}),
        },
        "rows": rows,
        "signals": signals,
    }


def sync_latest_quant_artifact() -> dict:
    """Download the newest GitHub Actions quant artifact for the website."""
    token = os.getenv("GITHUB_ACTIONS_TOKEN", "").strip()
    if not token:
        raise RuntimeError("GITHUB_ACTIONS_TOKEN 未配置，无法同步量化报告")
    repository = os.getenv("GITHUB_ACTIONS_REPOSITORY", "katelrving948-dotcom/a-share-trading").strip()
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "User-Agent": "a-share-research-hub",
    }
    request = Request(
        f"https://api.github.com/repos/{repository}/actions/artifacts?per_page=30",
        headers=headers,
    )
    with urlopen(request, timeout=30) as response:
        artifacts = json.loads(response.read().decode("utf-8")).get("artifacts", [])
    artifact = next(
        (item for item in artifacts if not item.get("expired") and str(item.get("name", "")).startswith("quant-factor-report-")),
        None,
    )
    if not artifact:
        raise RuntimeError("没有找到可用的量化报告 Artifact")
    with tempfile.TemporaryDirectory() as directory:
        archive = Path(directory) / "quant.zip"
        with urlopen(Request(artifact["archive_download_url"], headers=headers), timeout=60) as response:
            archive.write_bytes(response.read())
        expected = {
            "quant_report.html", "quant_summary.json", "quant_signals.csv",
            "quant_oos_equity.csv", "quant_folds.csv", "quant_factors_latest.csv",
            "quant_optimization_log.json",
        }
        QUANT_DIR.mkdir(parents=True, exist_ok=True)
        copied = []
        with zipfile.ZipFile(archive) as bundle:
            for member in bundle.infolist():
                name = Path(member.filename).name
                if member.is_dir() or name not in expected:
                    continue
                with bundle.open(member) as source, (QUANT_DIR / name).open("wb") as target:
                    shutil.copyfileobj(source, target)
                copied.append(name)
    if "quant_summary.json" not in copied or "quant_factors_latest.csv" not in copied:
        raise RuntimeError("量化 Artifact 缺少摘要或因子文件")
    return {"artifact": artifact.get("name"), "updated_at": artifact.get("updated_at"), "files": sorted(copied)}


def quant_model_gate(technical: dict) -> dict:
    metrics = (technical.get("summary") or {}).get("oos_metrics") or {}
    required = ("annual_return", "max_drawdown", "sharpe_ratio", "trading_days")
    if any(metrics.get(key) is None for key in required):
        return {
            "passed": False,
            "evaluated": False,
            "reason": "量化样本外指标不完整，停止用于选股和进场",
        }
    annual = float(metrics.get("annual_return") or 0)
    drawdown = float(metrics.get("max_drawdown") or 0)
    sharpe = float(metrics.get("sharpe_ratio") or 0)
    trading_days = int(metrics.get("trading_days") or 0)
    min_annual = float(os.getenv("QUANT_MIN_OOS_ANNUAL_RETURN", "0"))
    min_sharpe = float(os.getenv("QUANT_MIN_OOS_SHARPE", "0"))
    max_drawdown = float(os.getenv("QUANT_MAX_OOS_DRAWDOWN", "30"))
    min_days = int(os.getenv("QUANT_MIN_OOS_DAYS", "126"))
    checks = {
        "annual_return": annual > min_annual,
        "sharpe_ratio": sharpe > min_sharpe,
        "max_drawdown": drawdown >= -max_drawdown,
        "trading_days": trading_days >= min_days,
    }
    passed = all(checks.values())
    failed = [name for name, ok in checks.items() if not ok]
    return {
        "passed": passed,
        "evaluated": True,
        "reason": "样本外总闸门通过" if passed else f"样本外总闸门未通过：{', '.join(failed)}",
        "checks": checks,
        "thresholds": {
            "annual_return_gt": min_annual,
            "sharpe_ratio_gt": min_sharpe,
            "max_drawdown_gte": -max_drawdown,
            "trading_days_gte": min_days,
        },
        "metrics": metrics,
    }


def score_intersection(fundamental: dict, technical: dict) -> list[dict]:
    fundamental_min = float(os.getenv("PUSH_FUNDAMENTAL_MIN", "60"))
    technical_min = float(os.getenv("PUSH_TECHNICAL_MIN", "60"))
    display_limit = max(1, int(os.getenv("PUSH_DISPLAY_LIMIT", "20")))
    if not quant_model_gate(technical)["passed"]:
        return []
    technical_by_code = {str(row.get("code", "")).zfill(6): row for row in technical.get("rows", [])}
    merged = []
    for item in fundamental.get("rows", []):
        code = str(item.get("code", "")).zfill(6)
        factor = technical_by_code.get(code)
        if not factor:
            continue
        fundamental_score = float(item.get("fundamental_score") or 0)
        technical_score = float(factor.get("technical_score") or 0)
        if fundamental_score < fundamental_min or technical_score < technical_min:
            continue
        merged.append({
            "code": code,
            "name": item.get("name") or factor.get("name", ""),
            "industry": item.get("industry", ""),
            "fundamental_score": fundamental_score,
            "technical_score": technical_score,
            "combined_score": round((fundamental_score + technical_score) / 2, 2),
            "quality_score": item.get("quality_score"),
            "growth_score": item.get("growth_score"),
            "valuation_score": item.get("valuation_score"),
            "cashflow_score": item.get("cashflow_score"),
            "momentum": factor.get("momentum"),
            "trend": factor.get("trend"),
            "volatility": factor.get("volatility"),
            "volume_ratio": factor.get("volume_ratio"),
            "rsi": factor.get("rsi"),
            "bollinger_position": factor.get("bollinger_position"),
            "atr_pct": factor.get("atr_pct"),
            "quant_model_passed": True,
        })
    merged.sort(key=lambda row: row["combined_score"], reverse=True)
    for rank, row in enumerate(merged[:display_limit], start=1):
        row["rank"] = rank
    return merged[:display_limit]


def build_morning_entry_plan(intraday: dict, atr_pct: float | None = None) -> dict:
    """Build a conditional afternoon plan from the completed morning session."""
    morning = (intraday or {}).get("morning_session") or {}
    opening = (intraday or {}).get("opening_30m") or {}
    window = morning if morning.get("completed") else opening
    window_label = "09:30-11:30" if morning.get("completed") else "09:30-10:00"
    base = {
        "window": window_label,
        "actionable": False,
        "levels_available": False,
        "status": window.get("status") or "上午盘分时数据不可用",
        "entry_zone": None,
        "breakout_trigger": None,
        "max_chase_price": None,
        "stop_zone": None,
        "take_profit_zones": [],
        "risk_pct": None,
        "reference_price": None,
        "execution_state": "等待上午盘数据完成",
        "reason": "需要完整上午盘价格、均价与量价承接数据。",
        "morning": window,
        "quant_atr_pct": round(float(atr_pct or 0) * 100, 2),
        "execution_note": "普通A股当日买入通常不能当日卖出；价位是条件计划，跳空时可能无法按计划成交。",
    }
    trade_date = str((intraday or {}).get("trade_date") or "").replace("-", "")[:8]
    today = datetime.now(SHANGHAI).strftime("%Y%m%d")
    if trade_date and trade_date != today:
        base.update(
            status="非当日分时数据",
            execution_state="等待下一个交易日上午盘",
            reason=f"分时数据属于{trade_date}，当前日期为{today}，不生成当日进场价位。",
        )
        return base
    if not window.get("completed"):
        return base
    try:
        open_price = float(window["open"])
        high = float(window["high"])
        low = float(window["low"])
        close = float(window["close"])
        vwap = float(window["vwap"])
        change_pct = float(window.get("change_pct") or 0)
        range_pct = float(window.get("range_pct") or 0)
        up_ratio = float(window.get("up_minute_ratio") or 0)
        close_position = float(window.get("close_position") or 0)
        above_vwap_ratio = float(window.get("above_vwap_ratio") or 0)
    except (KeyError, TypeError, ValueError):
        base.update(status="上午盘数据不完整", reason="缺少价格区间或分时均价，不能计算价位。")
        return base
    if min(open_price, high, low, close, vwap) <= 0 or high < low:
        base.update(status="上午盘数据异常", reason="价格字段无效，停止生成价位。")
        return base
    if change_pct > 5 or range_pct > 8:
        base.update(
            status="暂不追涨",
            reason=f"上午涨幅{change_pct:+.2f}%、振幅{range_pct:.2f}%，等待重新形成支撑。",
        )
        return base
    if close < vwap * 0.995 or close_position < 0.35 or above_vwap_ratio < 0.40:
        base.update(
            status="暂不进场",
            reason=(
                f"午盘价位于上午区间{close_position:.0%}位置，"
                f"站上均价时间占比{above_vwap_ratio:.0%}，承接尚未确认。"
            ),
        )
        return base

    strong = close >= vwap and close_position >= 0.65 and up_ratio >= 0.50
    entry_low = max(low, vwap * (0.995 if strong else 0.990))
    entry_high = min(high, vwap * (1.005 if strong else 1.002))
    if entry_high < entry_low:
        base.update(status="暂不进场", reason="上午均价与价格区间无法形成有效回踩区。")
        return base
    entry_mid = (entry_low + entry_high) / 2
    factor_atr = max(0.0, float(atr_pct or 0))
    risk_fraction = min(0.05, max(0.015, factor_atr * 1.2, range_pct / 100 * 0.6))
    stop_high = min(low * 0.997, entry_low * (1 - risk_fraction))
    stop_low = stop_high * 0.995
    risk_per_share = max(entry_mid - stop_high, entry_mid * 0.005)
    target_one = entry_mid + risk_per_share * 1.5
    target_two = entry_mid + risk_per_share * 2.5
    current_price = float((intraday or {}).get("close_price") or close)
    breakout = high * 1.002
    max_chase = high * 1.015
    actionable = True
    execution_state = "等待回踩进场区或放量突破确认"
    status = "上午强势承接" if strong else "上午均价承接"
    if current_price < stop_high:
        actionable = False
        status = "上午结构已失效"
        execution_state = "当前价已跌破结构止损位，不按原计划进场"
    elif current_price > max_chase:
        actionable = False
        status = "当前价格暂不追涨"
        execution_state = "当前价超过禁追线，等待重新形成支撑"
    elif entry_low <= current_price <= entry_high:
        execution_state = "当前价进入回踩进场区"
    elif current_price >= breakout:
        execution_state = "已触发突破确认"
    base.update({
        "actionable": actionable,
        "levels_available": True,
        "status": status,
        "entry_zone": {"low": round(entry_low, 2), "high": round(entry_high, 2)},
        "breakout_trigger": round(breakout, 2),
        "max_chase_price": round(max_chase, 2),
        "stop_zone": {"low": round(stop_low, 2), "high": round(stop_high, 2)},
        "take_profit_zones": [
            {"name": "第一止盈", "low": round(target_one, 2), "high": round(target_one * 1.008, 2), "risk_reward": 1.5},
            {"name": "第二止盈", "low": round(target_two, 2), "high": round(target_two * 1.012, 2), "risk_reward": 2.5},
        ],
        "risk_pct": round((entry_mid - stop_high) / entry_mid * 100, 2),
        "reference_price": round(current_price, 2),
        "execution_state": execution_state,
        "reason": (
            f"上午收盘{close:.2f}，均价{vwap:.2f}，收在区间{close_position:.0%}位置；"
            f"量化ATR参与止损距离计算。"
        ),
    })
    return base


def build_trade_decision(item: dict) -> dict:
    """Apply sector, fundamental, quant and live-entry gates in order."""
    board_score = item.get("board_strength_score")
    fundamental_score = float(item.get("fundamental_score") or 0)
    technical_score = float(item.get("technical_score") or 0)
    plan = item.get("morning_plan") or {}
    state = str(plan.get("execution_state") or "")
    board_pass = board_score is not None and float(board_score) >= 60
    fundamental_pass = fundamental_score >= 60
    model_pass = item.get("quant_model_passed", True) is True
    quant_pass = technical_score >= 60 and model_pass
    entry_pass = bool(plan.get("actionable")) and (
        "进入回踩进场区" in state or "已触发突破确认" in state
    )
    blocked = any(word in f"{plan.get('status', '')}{state}" for word in ("暂不", "失效", "异常"))
    if blocked or not (board_pass and fundamental_pass and quant_pass):
        status = "不交易"
    elif entry_pass:
        status = "可执行观察"
    else:
        status = "等待确认"
    reasons = []
    if not board_pass:
        reasons.append("板块资金/效应未达到60分")
    if not fundamental_pass:
        reasons.append("基本面未达到60分")
    if not quant_pass:
        reasons.append("量化因子未达到60分或样本外总闸门未通过")
    if board_pass and fundamental_pass and quant_pass and not entry_pass:
        reasons.append(plan.get("status") or "午后进场条件尚未触发")
    return {
        "status": status,
        "board_gate": {"score": board_score, "passed": board_pass},
        "fundamental_gate": {"score": fundamental_score, "passed": fundamental_pass},
        "quant_gate": {"score": technical_score, "model_passed": model_pass, "passed": quant_pass},
        "entry_gate": {"passed": entry_pass, "state": state},
        "reasons": reasons,
        "note": "量化因子参与选股与进场门槛，价位仍须由上午盘结构触发；排名不是买入指令。",
    }


def _capital_strength(market: dict, boards: list[dict]) -> dict:
    sector_rows = market.get("sector_flow") or []
    positive = [row for row in sector_rows if float(row.get("main_net_inflow") or 0) > 0]
    top_three = sum(float(row.get("main_net_inflow") or 0) for row in sector_rows[:3])
    average_change = sum(float(row.get("change_pct") or 0) for row in sector_rows[:10]) / max(1, len(sector_rows[:10]))
    if len(positive) >= 8 and top_three > 0 and average_change > 0:
        label = "强"
    elif len(positive) >= 4 and top_three > 0:
        label = "中等"
    else:
        label = "弱或分化"
    return {
        "label": label,
        "positive_sector_count": len(positive),
        "observed_sector_count": len(sector_rows),
        "top_three_main_net_inflow": round(top_three, 2),
        "top_ten_average_change_pct": round(average_change, 2),
        "strong_board_count": sum(float(board.get("rotation_score") or 0) >= 60 for board in boards),
        "method": "综合当日主力净流入、上涨扩散度、近5日持续性和板块涨跌表现；单位沿用数据源亿元。",
    }


def build_market_research(
    observations: list[dict],
    fundamental: dict,
    technical: dict,
    feed: DataFeed | None = None,
) -> dict:
    """Build the external-market → sector → stock → entry research chain."""
    feed = feed or DataFeed()
    try:
        news = feed.get_financial_news()
    except Exception:
        news = []
    try:
        external = feed.get_external_market_context(news=news, force_refresh=True)
    except Exception as exc:
        external = {"available": False, "markets": [], "events": [], "limitations": [str(exc)]}
    try:
        market = feed.get_market_context(external_context=external)
    except Exception as exc:
        market = {"market_stats": {}, "sector_flow": [], "message": str(exc)}
    codes = [item["code"] for item in observations]
    try:
        rotation = feed.get_rotation_matches(codes, top_n=8, external_context=external)
    except Exception as exc:
        rotation = {"boards": [], "matches": {code: [] for code in codes}, "message": str(exc)}

    sector_by_name = {row.get("name"): row for row in market.get("sector_flow", [])}
    boards = rotation.get("boards") or []
    for board in boards:
        flow_score = float(board.get("flow_score") or 0)
        external_score = float(board.get("external_score") or 50)
        has_external_signal = bool(board.get("external_signal_count"))
        board["rotation_score"] = round(flow_score * 0.8 + external_score * 0.2) if has_external_signal else round(flow_score)
        breadth = sector_by_name.get(board.get("name"), {})
        board["rise_count"] = breadth.get("rise_count")
        board["fall_count"] = breadth.get("fall_count")
        flow = float(board.get("main_net_inflow") or 0)
        change = float(board.get("change_pct") or 0)
        board["effect"] = (
            "资金流入且上涨扩散，板块效应较强" if flow > 0 and change > 0
            else "资金流入但价格未确认" if flow > 0
            else "资金流出，板块效应偏弱"
        )
    boards.sort(key=lambda row: float(row.get("rotation_score") or 0), reverse=True)
    for rank, board in enumerate(boards, start=1):
        board["rank"] = rank

    matches = rotation.get("matches") or {}
    for item in observations:
        stock_boards = sorted(
            matches.get(item["code"], []),
            key=lambda row: float(row.get("rotation_score") or row.get("flow_score") or 0),
            reverse=True,
        )
        item["matched_boards"] = stock_boards[:3]
        strongest = stock_boards[0] if stock_boards else None
        item["board_strength_score"] = strongest.get("rotation_score") if strongest else None
        item["primary_board"] = strongest

    def intraday_enrichment(item: dict) -> tuple[str, dict, dict]:
        try:
            intraday = feed.get_intraday_minute(item["code"])
        except Exception as exc:
            intraday = {"available": False, "error": str(exc)}
        try:
            flow = feed.get_intraday_stock_fund_flow(item["code"])
        except Exception as exc:
            flow = {"available": False, "error": str(exc)}
        return item["code"], build_morning_entry_plan(intraday, item.get("atr_pct")), flow

    enriched = {}
    if observations:
        with ThreadPoolExecutor(max_workers=min(6, len(observations))) as executor:
            futures = [executor.submit(intraday_enrichment, item) for item in observations]
            for future in as_completed(futures):
                code, plan, flow = future.result()
                enriched[code] = (plan, flow)
    for item in observations:
        item["morning_plan"], item["intraday_fund_flow"] = enriched.get(
            item["code"], (build_morning_entry_plan({}), {"available": False})
        )
        item["trade_decision"] = build_trade_decision(item)

    fundamental_by_code = {
        str(row.get("code", "")).zfill(6): row for row in fundamental.get("rows", [])
    }
    technical_by_code = {
        str(row.get("code", "")).zfill(6): row for row in technical.get("rows", [])
    }
    hot_core = []
    seen = set()
    for board in boards:
        if float(board.get("rotation_score") or 0) < 60 or float(board.get("main_net_inflow") or 0) <= 0:
            continue
        for leader in board.get("leaders") or []:
            code = str(leader.get("code", "")).zfill(6)
            if not code or code in seen:
                continue
            seen.add(code)
            fundamental_row = fundamental_by_code.get(code, {})
            technical_row = technical_by_code.get(code, {})
            hot_core.append({
                **leader,
                "code": code,
                "board_name": board.get("name"),
                "board_type": board.get("type"),
                "board_strength_score": board.get("rotation_score"),
                "fundamental_score": fundamental_row.get("fundamental_score"),
                "technical_score": technical_row.get("technical_score"),
                "quant_passed": float(technical_row.get("technical_score") or 0) >= 60,
                "state": "进入双评分复核" if float(technical_row.get("technical_score") or 0) >= 60 else "仅作板块龙头观察",
            })
            if len(hot_core) >= 10:
                break
        if len(hot_core) >= 10:
            break
    return {
        "market": market,
        "external_market": external,
        "rotation_boards": boards[:12],
        "capital_strength": _capital_strength(market, boards),
        "hot_core_candidates": hot_core,
        "news_count": len(news),
    }


def build_push_payload(refresh: bool = False, universe_limit: int | None = None) -> dict:
    fundamental = refresh_fundamental(universe_limit) if refresh else load_fundamental()
    technical = load_technical()
    model_gate = quant_model_gate(technical)
    observations = score_intersection(fundamental, technical)
    market_research = build_market_research(observations, fundamental, technical)
    now = datetime.now(SHANGHAI)
    return {
        "subject": f"{now:%Y-%m-%d} A股午间全链路观察",
        "generated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "schedule": "工作日12:00",
        "analysis_window": "前一交易日完整盘面 + 当日09:30-11:30上午盘",
        "execution_window": "13:00-14:00复核使用",
        **market_research,
        "fundamental_summary": fundamental.get("summary", {}),
        "technical_summary": technical.get("summary", {}),
        "quant_model_gate": model_gate,
        "observations": observations,
        "observation_count": len(observations),
        "rules": {
            "fundamental_min": float(os.getenv("PUSH_FUNDAMENTAL_MIN", "60")),
            "technical_min": float(os.getenv("PUSH_TECHNICAL_MIN", "60")),
            "display_limit": int(os.getenv("PUSH_DISPLAY_LIMIT", "20")),
            "quant_model_gate": model_gate,
            "meaning": "外盘与事件只作情景输入；按板块资金→基本面→量化因子→上午盘触发顺序形成观察，不自动下单",
        },
    }
