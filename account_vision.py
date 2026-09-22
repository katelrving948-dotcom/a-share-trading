"""Extract a reviewable account draft from a brokerage screenshot."""

from __future__ import annotations

import json
import os
import re
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


def complete_missing_codes(draft: dict) -> dict:
    """Validate every extracted identity before the frontend merges holdings."""
    holdings = draft.get("holdings", [])
    if not holdings:
        return draft
    try:
        from data_feed import DataFeed

        records = DataFeed().get_stock_list().to_dict("records")
        names = {}
        for record in records:
            name = "".join(str(record.get("name") or "").split())
            code = str(record.get("code") or "").strip()
            if name and re.fullmatch(r"[0-9]{6}", code):
                names.setdefault(name, set()).add(code)
    except Exception:
        names = None
    for row in holdings:
        name = "".join(str(row.get("name") or "").split())
        matches = names.get(name, set()) if names else set()
        original = str(row.get("code") or "").strip()
        row["code"] = None
        if len(matches) == 1:
            row["code"] = next(iter(matches))
            if original and original != row["code"]:
                note = f"识别代码 {original} 与名称不符，已按名称匹配为 {row['code']}，请核对"
            elif original:
                note = "代码与股票名称匹配，请核对后保存"
            else:
                note = "已按股票名称自动匹配代码，请核对后保存"
        elif len(matches) > 1:
            note = "名称对应多个代码，请在交易软件核对并手动填写"
        elif not names:
            note = "股票代码查询暂不可用，请手动填写或重新识别截图"
        else:
            note = "未找到完全一致的股票名称，请核对名称并手动填写代码"
        if original and not row["code"]:
            note += f"；原识别代码 {original} 未通过核验，已清空"
        row["code_match_note"] = note
    return draft


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
                        "不要计算或补全看不清的值。逐行读取所有持仓，每一行单独输出，不能遗漏或合并。股票代码仅在图片明确显示时抄录六位；没有显示必须返回 null，禁止猜测或填入示例代码。界面若提示清算维护或数据不准确，"
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
        return complete_missing_codes(draft)
    raise RuntimeError("截图识别未返回可用字段，请改用手动填写")
