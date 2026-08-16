"""Local weekday 16:30 scheduler for the quant pipeline (standard library only)."""

from __future__ import annotations

import argparse
import logging
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from quant_pipeline import run_pipeline


SHANGHAI = ZoneInfo("Asia/Shanghai")
LOGGER = logging.getLogger("quant_scheduler")


def next_run(now: datetime | None = None, hour: int = 16, minute: int = 30) -> datetime:
    current = now.astimezone(SHANGHAI) if now else datetime.now(SHANGHAI)
    candidate = current.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= current:
        candidate += timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate += timedelta(days=1)
    return candidate


def run_daemon(send_email: bool = True) -> None:
    while True:
        target = next_run()
        LOGGER.info("下一次量化任务：%s", target.strftime("%Y-%m-%d %H:%M:%S %Z"))
        while True:
            remaining = (target - datetime.now(SHANGHAI)).total_seconds()
            if remaining <= 0:
                break
            time.sleep(min(60, max(1, remaining)))
        try:
            run_pipeline(send_mail=send_email)
        except Exception:
            LOGGER.exception("量化定时任务失败")


def main() -> None:
    parser = argparse.ArgumentParser(description="A股量化因子收盘后定时任务")
    parser.add_argument("--once", action="store_true", help="立即执行一次")
    parser.add_argument("--no-email", action="store_true", help="不发送邮件")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if args.once:
        run_pipeline(send_mail=not args.no_email)
    else:
        run_daemon(send_email=not args.no_email)


if __name__ == "__main__":
    main()
