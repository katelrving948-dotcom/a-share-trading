"""Shared snapshots consumed by the three-page site and the noon email."""

from __future__ import annotations

import json
import math
import os
import shutil
import tempfile
import zipfile
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


def refresh_fundamental(universe_limit: int | None = None, progress_callback=None) -> dict:
    scorer = FundamentalScorer(DataFeed())
    rows = scorer.score(universe_limit=universe_limit, progress_callback=progress_callback)
    payload = {"summary": scorer.summary, "rows": rows}
    save_json(FUNDAMENTAL_FILE, payload)
    return _clean(payload)


def load_fundamental() -> dict:
    if not FUNDAMENTAL_FILE.exists():
        return {"summary": {"state": "missing", "message": "尚未生成基本面快照"}, "rows": []}
    try:
        return json.loads(FUNDAMENTAL_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"summary": {"state": "error", "message": f"基本面快照读取失败：{exc}"}, "rows": []}


def load_technical() -> dict:
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


def score_intersection(fundamental: dict, technical: dict) -> list[dict]:
    fundamental_min = float(os.getenv("PUSH_FUNDAMENTAL_MIN", "60"))
    technical_min = float(os.getenv("PUSH_TECHNICAL_MIN", "60"))
    display_limit = max(1, int(os.getenv("PUSH_DISPLAY_LIMIT", "20")))
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
        })
    merged.sort(key=lambda row: row["combined_score"], reverse=True)
    for rank, row in enumerate(merged[:display_limit], start=1):
        row["rank"] = rank
    return merged[:display_limit]


def market_context() -> dict:
    feed = DataFeed()
    try:
        metrics = feed.get_market_metrics() or {}
    except Exception as exc:
        metrics = {"available": False, "message": str(exc)}
    return _clean(metrics)


def build_push_payload(refresh: bool = False, universe_limit: int | None = None) -> dict:
    fundamental = refresh_fundamental(universe_limit) if refresh else load_fundamental()
    technical = load_technical()
    observations = score_intersection(fundamental, technical)
    now = datetime.now(SHANGHAI)
    return {
        "subject": f"{now:%Y-%m-%d} A股双评分午间观察",
        "generated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "schedule": "工作日12:00",
        "analysis_window": "前一交易日完整盘面 + 当日09:30-11:30上午盘",
        "execution_window": "13:00-14:00复核使用",
        "market": market_context(),
        "fundamental_summary": fundamental.get("summary", {}),
        "technical_summary": technical.get("summary", {}),
        "observations": observations,
        "observation_count": len(observations),
        "rules": {
            "fundamental_min": float(os.getenv("PUSH_FUNDAMENTAL_MIN", "60")),
            "technical_min": float(os.getenv("PUSH_TECHNICAL_MIN", "60")),
            "display_limit": int(os.getenv("PUSH_DISPLAY_LIMIT", "20")),
            "meaning": "两类评分的自然交集，仅为观察池；不生成交易计划或自动下单",
        },
    }
