"""Public hourly news and once-per-session US closing snapshots."""

from __future__ import annotations

import json
import math
import re
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from data_feed import DataFeed
from research_core import RESEARCH_DIR, _read_json, _replace_bytes, sync_public_snapshots


SNAPSHOT_FILE = RESEARCH_DIR / "market_news.json"
SHANGHAI = ZoneInfo("Asia/Shanghai")
NEW_YORK = ZoneInfo("America/New_York")
US_INDICES = ((".DJI", "道琼斯"), (".INX", "标普500"), (".IXIC", "纳斯达克综合"))
GEO_TERMS = (
    "战争", "冲突", "停火", "空袭", "袭击", "军事", "导弹", "无人机", "制裁",
    "俄乌", "俄罗斯", "乌克兰", "中东", "伊朗", "以色列", "加沙", "黎巴嫩",
    "胡塞", "红海", "霍尔木兹", "北约", "地缘", "贸易谈判", "航运", "油轮",
)
DOMESTIC_TERMS = (
    "央行", "人民银行", "财政部", "证监会", "金融监管总局", "国务院", "发改委",
    "降准", "降息", "逆回购", "公开市场", "货币政策", "财政政策", "专项债",
    "国债", "人民币", "A股", "沪指", "深成指", "创业板", "科创板", "沪深",
    "上交所", "深交所", "北交所", "港股", "社融", "LPR", "存款利率",
    "中国经济", "金融监管", "证券", "银行", "保险", "房地产", "政府采购",
)
FOREIGN_TERMS = ("美国", "欧洲", "日本", "韩国", "英国", "法国", "德国", "印度", "俄罗斯", "乌克兰", "伊朗", "巴西", "澳大利亚", "加拿大", "美联储")
CHINA_TERMS = ("中国", "我国", "人民币", "人民银行", "A股", "沪指", "深成指", "沪深", "上交所", "深交所", "北交所", "港交所", "香港")
US_TERMS = (
    "美股", "道琼斯", "标普", "纳斯达克", "美联储", "美债", "中概",
    "美国经济", "美国通胀", "美国就业", "美国CPI", "美国非农", "美国国债", "美国财长",
    "英伟达", "特斯拉", "苹果公司", "微软", "亚马逊", "谷歌", "Meta",
)


def classify_news(rows: list[dict], now: datetime) -> dict[str, list[dict]]:
    """Select reported headlines; category matching does not infer market impact."""
    result = {key: [] for key in ("geopolitics", "domestic", "us")}
    seen = set()
    for row in sorted(rows, key=lambda item: item.get("time", ""), reverse=True):
        title = str(row.get("title") or "").strip()
        summary = str(row.get("summary") or "").strip()
        try:
            published = datetime.fromisoformat(row.get("time", "")).replace(tzinfo=SHANGHAI)
        except (TypeError, ValueError):
            continue
        if not title or "\ufffd" in title + summary or not timedelta(0) <= now - published <= timedelta(hours=72):
            continue
        key = row.get("url") or title
        if key in seen:
            continue
        seen.add(key)
        text = title + " " + summary
        for category, terms in (("geopolitics", GEO_TERMS), ("domestic", DOMESTIC_TERMS), ("us", US_TERMS)):
            if not any(term.lower() in text.lower() for term in terms):
                continue
            # Broad finance words alone must not turn foreign news into domestic news.
            if category == "domestic" and any(term in text for term in FOREIGN_TERMS) and not any(term in text for term in CHINA_TERMS):
                continue
            result[category].append({
                "title": title, "summary": summary, "time": row["time"],
                "source": str(row.get("source") or "东方财富"),
                "url": str(row.get("url") or ""),
            })
    return {key: rows[:30] for key, rows in result.items()}


def latest_closed_weekday(now: datetime):
    local = now.astimezone(NEW_YORK)
    day = local.date()
    if (local.hour, local.minute) < (16, 15):
        day -= timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


def get_us_quotes(feed: DataFeed) -> list[dict]:
    response = feed._request("https://qt.gtimg.cn/q=" + ",".join("us" + symbol for symbol, _ in US_INDICES))
    if response is None:
        return []
    # This endpoint uses GBK. Display names are fixed, rather than decoded labels.
    quotes = dict(re.findall(r'v_us(\.[A-Z]+)="([^"]*)";', response.content.decode("gb18030")))
    result = []
    for symbol, name in US_INDICES:
        values = quotes.get(symbol, "").split("~")
        if len(values) <= 32:
            continue
        try:
            price, change = float(values[3]), float(values[32])
            timestamp = datetime.strptime(values[30], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
        if not math.isfinite(price) or not math.isfinite(change) or price <= 0:
            continue
        result.append({"symbol": symbol, "name": name, "price": price,
                       "change_pct": change, "as_of": timestamp.isoformat(sep=" ")})
    return result


def collect_snapshot(feed: DataFeed | None = None, now: datetime | None = None,
                     path: Path | None = None) -> dict:
    feed = feed or DataFeed()
    now = (now or datetime.now(SHANGHAI)).astimezone(SHANGHAI)
    path = path or SNAPSHOT_FILE
    payload = _read_json(path)
    stamp = now.strftime("%Y-%m-%d %H:%M:%S")
    sections = payload.setdefault("news", {})
    warnings = []
    rows = []
    for fetch in (lambda: feed.get_financial_news(100), lambda: feed.get_external_news(100)):
        try:
            rows.extend(fetch())
        except Exception as exc:
            warnings.append(type(exc).__name__)
    classified = classify_news(rows, now)
    for category, items in classified.items():
        section = sections.setdefault(category, {"items": [], "generated_at": ""})
        section["items"] = classify_news(section["items"], now)[category]
        if items:
            section.update(items=items, generated_at=stamp, error="")
        else:
            section["error"] = "本次未取得有效快讯，保留上次成功内容。" if section["items"] else "本次未取得该分类的有效快讯。"
    payload["news_warnings"] = warnings
    expected = latest_closed_weekday(now).isoformat()
    previous = payload.get("us_market") or {}
    if previous.get("session_date", "") < expected:
        try:
            quotes = get_us_quotes(feed)
            dates = {row["as_of"][:10] for row in quotes}
            # Never replace a complete close with a partial, mixed-date or intraday set.
            if len(quotes) != 3 or len(dates) != 1:
                raise ValueError("三大指数数据缺失或日期不一致")
            session = next(iter(dates))
            if session > expected or any(row["as_of"][11:16] < "15:55" for row in quotes):
                raise ValueError("行情日期或时间尚不能确认常规收盘；提前收市需另行复核")
            if session <= previous.get("session_date", ""):
                raise ValueError("休市或数据源尚未提供新交易日行情")
            payload["us_market"] = {"session_date": session, "generated_at": stamp,
                                    "source": "腾讯财经", "markets": quotes}
            payload["us_market_error"] = "" if session == expected else "休市或行情源尚未更新，展示最近可得收盘数据。"
        except Exception as exc:
            payload["us_market_error"] = "本次收盘行情更新未完成，保留最近成功数据：" + str(exc)
    payload.update(schema_version=1, generated_at=stamp)
    _replace_bytes(path, json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8"))
    return payload


def load_news_snapshot() -> dict:
    sync_public_snapshots(background=True)
    return _read_json(SNAPSHOT_FILE)


if __name__ == "__main__":
    result = collect_snapshot()
    print(json.dumps({"generated_at": result["generated_at"],
                      "news_counts": {key: len(section["items"]) for key, section in result["news"].items()},
                      "us_session": result.get("us_market", {}).get("session_date"),
                      "us_error": result.get("us_market_error", "")}, ensure_ascii=False))
