#!/usr/bin/env python3
"""Three-core HTTP service: push chain, fundamentals, and technical factors."""

from __future__ import annotations

import base64
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
    build_account_holding_actions, build_push_payload, load_fundamental, load_technical,
    refresh_fundamental, sync_latest_quant_artifact,
)
from account_vision import extract_account_screenshot
from weekly_strategy import ACCOUNT_STATE_FILE, load_account_state, save_account_update


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
    return current.weekday() < 5 and 730 <= current.hour * 100 + current.minute <= 830


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


def _sync_account_state_secret() -> dict:
    """Persist the private state for the next GitHub Actions email run."""
    token = os.getenv("GITHUB_ACTIONS_TOKEN", "").strip()
    if not token or os.getenv("ACCOUNT_SECRET_SYNC", "1") != "1":
        return {"state": "local_only", "message": "未配置GitHub私密持久化；Render重启后需重新录入"}
    repository = os.getenv("GITHUB_ACTIONS_REPOSITORY", "katelrving948-dotcom/a-share-trading").strip()
    secret_name = os.getenv("ACCOUNT_STATE_SECRET_NAME", "ACCOUNT_STATE_JSON").strip()
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "User-Agent": "a-share-research-hub",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    key_request = Request(
        f"https://api.github.com/repos/{repository}/actions/secrets/public-key",
        headers=headers,
    )
    with urlopen(key_request, timeout=30) as response:
        key_data = json.loads(response.read().decode("utf-8"))
    from nacl import encoding, public
    public_key = public.PublicKey(key_data["key"].encode("utf-8"), encoding.Base64Encoder())
    encrypted = public.SealedBox(public_key).encrypt(ACCOUNT_STATE_FILE.read_bytes())
    update_request = Request(
        f"https://api.github.com/repos/{repository}/actions/secrets/{secret_name}",
        data=json.dumps({
            "encrypted_value": base64.b64encode(encrypted).decode("ascii"),
            "key_id": key_data["key_id"],
        }).encode("utf-8"),
        headers={**headers, "Content-Type": "application/json"},
        method="PUT",
    )
    with urlopen(update_request, timeout=30) as response:
        if response.status not in (201, 204):
            raise RuntimeError(f"GitHub私密持久化返回 HTTP {response.status}")
    return {"state": "synced", "message": "已写入GitHub加密Secret，将用于下一次邮件推送"}


def _public_push_payload(payload: dict) -> dict:
    public = json.loads(json.dumps(payload, ensure_ascii=False))
    account = public.get("account") or {}
    holdings = account.pop("holdings", [])
    account["holdings_count"] = len(holdings)
    for key in (
        "equity", "available_cash", "last_week_pnl", "last_week_return_pct",
        "current_week_pnl", "current_week_return_pct", "holdings_value",
        "holdings_pct", "holdings_planned_risk", "updated_at", "source_as_of",
    ):
        account.pop(key, None)
    weekly = public.get("weekly_plan") or {}
    weekly["holding_actions"] = []
    weekly_account = weekly.get("account") or {}
    weekly_account.pop("holdings", None)
    for key in (
        "equity", "available_cash", "last_week_pnl", "last_week_return_pct",
        "current_week_pnl", "current_week_return_pct", "holdings_value",
        "holdings_pct", "holdings_planned_risk", "updated_at", "source_as_of",
    ):
        weekly_account.pop(key, None)
    return public


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
                return self._json({"status": "ok", "system": "weekly-trend-risk", "time": _now()})
            if path in ("/api/push/status", "/api/cron/daily-email/status"):
                return self._json(self._push_status())
            if path == "/api/push/preview":
                return self._json(_public_push_payload(build_push_payload(refresh=False)))
            if path == "/api/fundamental":
                payload = load_fundamental()
                payload["task"] = _snapshot(_fundamental_state)
                return self._json(payload)
            if path == "/api/fundamental/status":
                return self._json(_snapshot(_fundamental_state))
            if path == "/api/technical":
                return self._json(load_technical())
            if path == "/api/account":
                if not self._authorized():
                    return self._json({"error": "未授权"}, 401)
                return self._json(load_account_state())
            if path == "/api/account/analysis":
                if not self._authorized():
                    return self._json({"error": "未授权"}, 401)
                account = load_account_state()
                return self._json({
                    "account": account,
                    "holding_actions": build_account_holding_actions(account),
                })
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
                        reason="非工作日07:30-08:30推送窗口，未提前生成或发送",
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
            if path == "/api/account":
                if not self._authorized():
                    return self._json({"error": "未授权"}, 401)
                account = save_account_update(body, ACCOUNT_STATE_FILE)
                try:
                    persistence = _sync_account_state_secret()
                except Exception as exc:
                    persistence = {"state": "local_only", "message": f"本次已保存，但私密持久化失败：{exc}"}
                account["persistence"] = persistence
                return self._json(account)
            if path == "/api/account/extract":
                if not self._authorized():
                    return self._json({"error": "未授权"}, 401)
                return self._json(extract_account_screenshot(body.get("image_data_url") or ""))
            return self._json({"error": "接口不存在"}, 404)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            return self._json({"error": str(exc)}, 400)
        except Exception as exc:
            return self._json({"error": str(exc)}, 500)

    def _push_status(self) -> dict:
        state = _snapshot(_push_state)
        state.update({
            "schedule": "工作日08:00 Asia/Shanghai（周计划周一生成，持仓建议每日刷新）",
            "analysis_window": "最新可得收盘行情 + 已确认持仓 + 周度固定计划与事件风险",
            "chain": ["工作日触发", "私密持仓", "最新收盘分析", "周度固定计划", "邮件服务"],
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
        if length > 10_500_000:
            raise ValueError("请求过大")
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
    print(f"A股周度趋势与风险系统：http://localhost:{port}")
    print("模块：周度计划 / 基本面评分 / 技术面量化")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
