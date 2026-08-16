#!/usr/bin/env python3
"""Three-core HTTP service: push chain, fundamentals, and technical factors."""

from __future__ import annotations

import hmac
import json
import mimetypes
import os
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from research_core import (
    build_push_payload, load_fundamental, load_technical,
    refresh_fundamental, sync_latest_quant_artifact,
)


ROOT = Path(__file__).resolve().parent
TEMPLATE = ROOT / "templates" / "index.html"
STATIC_DIR = ROOT / "static"
SHANGHAI = ZoneInfo("Asia/Shanghai")

_state_lock = threading.RLock()
_push_state = {"state": "idle", "started_at": None, "completed_at": None, "error": None}
_fundamental_state = {"state": "idle", "started_at": None, "completed_at": None, "error": None, "progress": {}}


def _now() -> str:
    return datetime.now(SHANGHAI).strftime("%Y-%m-%d %H:%M:%S")


def _scheduled_push_allowed(now: datetime | None = None) -> bool:
    if os.getenv("CRON_WINDOW_BYPASS", "0") == "1":
        return True
    current = now or datetime.now(SHANGHAI)
    return current.weekday() < 5 and 1150 <= current.hour * 100 + current.minute <= 1230


def _snapshot(target: dict) -> dict:
    with _state_lock:
        return json.loads(json.dumps(target, ensure_ascii=False))


def _update(target: dict, **values) -> None:
    with _state_lock:
        target.update(values)


def _dispatch_daily_email_workflow(force: bool = False) -> None:
    token = os.getenv("GITHUB_ACTIONS_TOKEN", "").strip()
    if not token:
        raise RuntimeError("GITHUB_ACTIONS_TOKEN 未配置")
    repository = os.getenv("GITHUB_ACTIONS_REPOSITORY", "katelrving948-dotcom/a-share-trading").strip()
    workflow = os.getenv("GITHUB_ACTIONS_WORKFLOW", "daily-stock-email.yml").strip()
    ref = os.getenv("GITHUB_ACTIONS_REF", "main").strip()
    request = Request(
        f"https://api.github.com/repos/{repository}/actions/workflows/{workflow}/dispatches",
        data=json.dumps({"ref": ref, "inputs": {"force_send": force}}).encode("utf-8"),
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "a-share-research-hub",
        },
        method="POST",
    )
    with urlopen(request, timeout=30) as response:
        if response.status != 204:
            raise RuntimeError(f"GitHub Actions 返回 HTTP {response.status}")


def _run_push_dispatch(force: bool = False) -> None:
    _update(_push_state, state="running", started_at=_now(), completed_at=None, error=None)
    try:
        if force:
            _dispatch_daily_email_workflow(force=True)
        else:
            _dispatch_daily_email_workflow()
    except Exception as exc:
        _update(_push_state, state="failed", completed_at=_now(), error=str(exc))
    else:
        _update(_push_state, state="dispatched", completed_at=_now(), error=None)


def _start_push_dispatch(force: bool = False) -> bool:
    with _state_lock:
        if _push_state["state"] == "running":
            return False
        _push_state.update({"state": "queued", "started_at": _now(), "completed_at": None, "error": None})
    threading.Thread(target=_run_push_dispatch, args=(force,), daemon=True).start()
    return True


def _run_fundamental_refresh(limit: int | None) -> None:
    _update(_fundamental_state, state="running", started_at=_now(), completed_at=None, error=None, progress={})

    def progress(value):
        _update(_fundamental_state, progress=value)

    try:
        payload = refresh_fundamental(limit, progress_callback=progress)
    except Exception as exc:
        _update(_fundamental_state, state="failed", completed_at=_now(), error=str(exc))
    else:
        _update(
            _fundamental_state,
            state="done",
            completed_at=_now(),
            error=None,
            result_count=len(payload.get("rows", [])),
        )


def _start_fundamental_refresh(limit: int | None) -> bool:
    with _state_lock:
        if _fundamental_state["state"] == "running":
            return False
        _fundamental_state.update({"state": "queued", "started_at": _now(), "completed_at": None, "error": None})
    threading.Thread(target=_run_fundamental_refresh, args=(limit,), daemon=True).start()
    return True


class ApiHandler(BaseHTTPRequestHandler):
    server_version = "ResearchHub/1.0"

    def log_message(self, format, *args):
        print(f"[{self.log_date_time_string()}] {format % args}")

    def do_GET(self):
        path = urlparse(self.path).path
        try:
            if path in ("/", "/index.html"):
                return self._send_file(TEMPLATE, "text/html; charset=utf-8")
            if path.startswith("/static/"):
                return self._send_static(path)
            if path == "/api/status":
                return self._json({"status": "ok", "system": "three-core-research", "time": _now()})
            if path in ("/api/push/status", "/api/cron/daily-email/status"):
                return self._json(self._push_status())
            if path == "/api/push/preview":
                return self._json(build_push_payload(refresh=False))
            if path == "/api/fundamental":
                payload = load_fundamental()
                payload["task"] = _snapshot(_fundamental_state)
                return self._json(payload)
            if path == "/api/fundamental/status":
                return self._json(_snapshot(_fundamental_state))
            if path == "/api/technical":
                return self._json(load_technical())
            if path == "/api/cron/wake":
                self.send_response(204)
                self.end_headers()
                return
            return self._json({"error": "接口不存在"}, 404)
        except Exception as exc:
            return self._json({"error": str(exc)}, 500)

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            body = self._read_json()
            if path in ("/api/push/run", "/api/cron/daily-email"):
                if not self._authorized():
                    return self._json({"error": "未授权"}, 401)
                if path == "/api/cron/daily-email" and not _scheduled_push_allowed():
                    _update(
                        _push_state,
                        state="skipped",
                        completed_at=_now(),
                        error=None,
                        reason="非工作日12:00推送窗口，未提前生成或发送",
                    )
                    self.send_response(204)
                    self.end_headers()
                    return
                started = _start_push_dispatch(force=path == "/api/push/run")
                if path == "/api/cron/daily-email":
                    self.send_response(204 if started else 409)
                    self.end_headers()
                    return
                return self._json({"started": started, "status": self._push_status()}, 202 if started else 409)
            if path == "/api/fundamental/run":
                limit = body.get("universe_limit")
                if limit is not None:
                    limit = max(0, int(limit))
                started = _start_fundamental_refresh(limit)
                return self._json({"started": started, "status": _snapshot(_fundamental_state)}, 202 if started else 409)
            if path == "/api/technical/sync":
                if not self._authorized():
                    return self._json({"error": "未授权"}, 401)
                return self._json({"synced": sync_latest_quant_artifact(), "technical": load_technical()})
            return self._json({"error": "接口不存在"}, 404)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            return self._json({"error": str(exc)}, 400)
        except Exception as exc:
            return self._json({"error": str(exc)}, 500)

    def _push_status(self) -> dict:
        state = _snapshot(_push_state)
        state.update({
            "schedule": "工作日12:00 Asia/Shanghai",
            "analysis_window": "前一交易日完整盘面 + 当日09:30-11:30上午盘",
            "chain": ["cron-job.org", "Render受保护接口", "GitHub Actions", "评分报告生成", "邮件服务"],
            "workflow_configured": bool(os.getenv("GITHUB_ACTIONS_TOKEN", "").strip()),
            "delivery_boundary": "dispatched只表示工作流已触发；收件箱是最终送达凭证",
        })
        return state

    def _authorized(self) -> bool:
        secret = os.getenv("CRON_SECRET", "").strip()
        if not secret:
            return False
        provided = self.headers.get("Authorization", "")
        expected = f"Bearer {secret}"
        return hmac.compare_digest(provided, expected)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0") or 0)
        if not length:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def _json(self, payload: dict, status: int = 200):
        data = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _send_static(self, request_path: str):
        relative = request_path.removeprefix("/static/")
        target = (STATIC_DIR / relative).resolve()
        if STATIC_DIR.resolve() not in target.parents or not target.is_file():
            return self._json({"error": "文件不存在"}, 404)
        mime = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        return self._send_file(target, mime)

    def _send_file(self, path: Path, content_type: str):
        if not path.is_file():
            return self._json({"error": "文件不存在"}, 404)
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def main() -> None:
    port = int(os.getenv("PORT", "5000"))
    server = ThreadingHTTPServer(("0.0.0.0", port), ApiHandler)
    print(f"A股三核研究系统：http://localhost:{port}")
    print("模块：推送中心 / 基本面评分 / 技术面量化")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
