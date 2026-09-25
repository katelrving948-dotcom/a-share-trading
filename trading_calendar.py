"""Known SSE closures; dates are exchange holidays, not adjusted workdays.

2026 source: https://www.sse.com.cn/disclosure/dealinstruc/closed/c/c_20251222_10802510.shtml
Future weekday holidays must be added when the exchange publishes them.
"""
from datetime import date


HOLIDAYS_2026 = (
    ("01-01", "01-03", "元旦"), ("02-15", "02-23", "春节"),
    ("04-04", "04-06", "清明节"), ("05-01", "05-05", "劳动节"),
    ("06-19", "06-21", "端午节"), ("09-25", "09-27", "中秋节"),
    ("10-01", "10-07", "国庆节"),
)


def market_closed_reason(day: date) -> str:
    if day.year == 2026:
        mmdd = day.strftime("%m-%d")
        for start, end, name in HOLIDAYS_2026:
            if start <= mmdd <= end:
                return f"{name}休市"
    return "周末休市" if day.weekday() >= 5 else ""
