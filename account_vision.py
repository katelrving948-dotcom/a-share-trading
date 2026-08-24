"""Extract a reviewable account draft from a brokerage screenshot."""

from __future__ import annotations

import json
import os
from urllib.request import Request, urlopen


ACCOUNT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "equity": {"type": ["number", "null"]},
        "available_cash": {"type": ["number", "null"]},
        "as_of": {"type": ["string", "null"]},
        "screen_warning": {"type": ["string", "null"]},
        "holdings": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "code": {"type": ["string", "null"]},
                    "name": {"type": ["string", "null"]},
                    "quantity": {"type": ["integer", "null"]},
                    "available_quantity": {"type": ["integer", "null"]},
                    "cost_price": {"type": ["number", "null"]},
                    "current_price": {"type": ["number", "null"]},
                    "market_value": {"type": ["number", "null"]},
                    "confidence": {"type": "string", "enum": ["高", "中", "低"]},
                    "review_note": {"type": ["string", "null"]},
                },
                "required": [
                    "code", "name", "quantity", "available_quantity", "cost_price",
                    "current_price", "market_value", "confidence", "review_note",
                ],
            },
        },
    },
    "required": ["equity", "available_cash", "as_of", "screen_warning", "holdings"],
}


def extract_account_screenshot(image_data_url: str) -> dict:
    api_key = os.getenv("DASHSCOPE_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("DASHSCOPE_API_KEY 未配置，暂不能识别截图；仍可手动填写持仓")
    if not isinstance(image_data_url, str) or not image_data_url.startswith(
        ("data:image/jpeg;base64,", "data:image/png;base64,", "data:image/webp;base64,")
    ):
        raise ValueError("只支持 JPG、PNG 或 WEBP 持仓截图")
    if len(image_data_url) > 10_000_000:
        raise ValueError("截图过大，请压缩到约7MB以内")

    payload = {
        "model": os.getenv("DASHSCOPE_VISION_MODEL", "qwen3-vl-plus"),
        "messages": [{
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": (
                        "读取这张中国券商持仓截图，仅抄录清晰可见的数据。不要推测被遮挡账号，"
                        "不要计算或补全看不清的值。股票代码保留六位。界面若提示清算维护或数据不准确，"
                        "写入 screen_warning。每只股票给出识别置信度和需要人工复核的字段。"
                        "严格按照指定 JSON 结构输出；无法识别的可空字段使用 null。"
                    ),
                },
                {"type": "image_url", "image_url": {"url": image_data_url}},
            ],
        }],
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "broker_account_draft",
                "strict": True,
                "schema": ACCOUNT_SCHEMA,
            },
        },
        "enable_thinking": False,
    }
    base_url = os.getenv(
        "DASHSCOPE_BASE_URL",
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
    ).strip().rstrip("/")
    request = Request(
        f"{base_url}/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "a-share-research-hub",
        },
        method="POST",
    )
    with urlopen(request, timeout=90) as response:
        result = json.loads(response.read().decode("utf-8"))
    choices = result.get("choices") or []
    if choices:
        content = (choices[0].get("message") or {}).get("content") or ""
        draft = json.loads(content)
        draft["source"] = "screenshot_bailian_draft"
        draft["confirmed"] = False
        return draft
    raise RuntimeError("截图识别未返回可用字段，请改用手动填写")
