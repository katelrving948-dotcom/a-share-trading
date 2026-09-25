"""Combine completed daily trend with strictly dated morning observations."""
import math

from weekly_strategy import build_holding_action
from trading_calendar import market_closed_reason


def build_noon_action(holding, trend, account, stock, market, sector, now, sector_name=""):
    closed = market_closed_reason(now.date())
    contexts = {"个股": stock, "沪深300": market, "行业板块": sector}
    issues, evidence, valid_sources = [], {}, {}
    for label, quote in contexts.items():
        session = (quote or {}).get("morning_session") or {}
        date = str((quote or {}).get("trade_date") or "").replace("-", "")
        time = str(session.get("last_time") or "").replace(":", "")
        numbers = [session.get(key) for key in ("open", "close", "vwap", "volume")]
        valid = all(isinstance(v, (int, float)) and math.isfinite(v) and v > 0 for v in numbers)
        valid_sources[label] = not (not (quote or {}).get("available") or not session.get("completed") or not valid
                or date != now.strftime("%Y%m%d") or time != "1130"
                or now.hour * 100 + now.minute < 1130 or bool(closed))
        if not valid_sources[label]:
            detail = (quote or {}).get("error") or ("行情日期不符" if date != now.strftime("%Y%m%d")
                     else "上午行情未完成或数值无效")
            issues.append(f"{label}：{detail}")
        evidence[label] = {**session, "trade_date": date, "valid": valid_sources[label]}
    previous = build_holding_action(holding, trend, account)
    result = {**previous, "analysis_window": "前日趋势 + 当日09:30-11:30",
              "generated_at": now.isoformat(), "morning_evidence": evidence,
              "sector_name": sector_name, "quote_as_of": None, "morning_ready": not issues}
    if issues or not trend.get("available"):
        result.update(action="休市观察" if closed else "等待上午行情复核", sell_quantity=0, t_eligible=False, morning_ready=False,
                      reference_price=None, pnl_pct=None,
                      reason=(closed + "，不生成当日交易动作") if closed else "；".join(issues or ["前日趋势数据不足"]))
        if valid_sources["个股"]:
            price = stock["morning_session"]["close"]
            cost = holding.get("cost_price") or 0
            result.update(reference_price=price, quote_as_of=now.strftime("%Y-%m-%d") + " 11:30",
                          pnl_pct=round((price / cost - 1) * 100, 2) if cost > 0 else None)
        return result
    morning = stock["morning_session"]
    result.update(build_holding_action(holding, {**trend, "close": morning["close"]}, account))
    result["quote_as_of"] = now.strftime("%Y-%m-%d") + " 11:30"
    weak = [label for label, q in contexts.items()
            if q["morning_session"]["close"] < q["morning_session"]["vwap"]]
    details = [f"{label}开盘至午收{(q['morning_session']['close']/q['morning_session']['open']-1)*100:+.2f}%"
               for label, q in contexts.items()]
    result["reason"] += "；" + "，".join(details)
    if weak:
        result["reason"] += "；" + "、".join(weak) + "午收低于上午均价，暂缓加仓"
        result["t_eligible"] = False
        if result["action"] == "持有":
            result["action"] = "持有观察，暂缓加仓"
    return result
