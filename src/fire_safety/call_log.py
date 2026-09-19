"""Persistent JSONL audit log for model analysis calls."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from threading import Lock
from typing import Any

from fire_safety.settings import Settings
from fire_safety.timing import TimingReport

_WRITE_LOCK = Lock()
_METHOD_NAMES = {
    "llamacpp": "Llama.cpp",
    "dashscope": "阿里云百炼",
    "vllm": "vLLM",
}
_STAGE_FIELDS = {
    "图片预处理": "image_preprocessing_seconds",
    "模型请求": "model_request_seconds",
    "后续处理": "post_processing_seconds",
}


def build_call_record(
    *, status: str, timing: TimingReport, settings: Settings
) -> dict[str, Any]:
    """Build one stable, flat record suitable for JSONL and tabular analysis."""

    record: dict[str, Any] = {
        "schema_version": "1.0",
        "timestamp": datetime.now().astimezone().isoformat(timespec="milliseconds"),
        "status": status,
        "invocation_method": _METHOD_NAMES[settings.qwen_provider],
        "model_name": settings.qwen_model,
        "total_seconds": round(timing.total_seconds, 6),
        "image_preprocessing_seconds": None,
        "model_request_seconds": None,
        "post_processing_seconds": None,
        "ttfb_seconds": None,
        "ttft_seconds": None,
        "prompt_tokens": None,
        "completion_tokens": None,
        "reasoning_tokens": None,
    }
    for stage in timing.stages:
        if field := _STAGE_FIELDS.get(stage.name):
            record[field] = round(stage.seconds, 6)
    for item in timing.notes:
        if item.name in {"ttfb", "ttft"}:
            record[f"{item.name}_seconds"] = _parse_seconds(item.value)
        elif item.name in {"prompt_tokens", "completion_tokens", "reasoning_tokens"}:
            record[item.name] = item.value
    return record


def append_call_record(
    *, status: str, timing: TimingReport, settings: Settings
) -> dict[str, Any]:
    """Append one complete call record and return the serialized payload."""

    record = build_call_record(status=status, timing=timing, settings=settings)
    line = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
    path = Path(settings.call_log_path)
    with _WRITE_LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as log_file:
            log_file.write(line)
    return record


def _parse_seconds(value: object) -> float | None:
    text = str(value).strip()
    if text.endswith("s"):
        text = text[:-1]
    try:
        return float(text)
    except ValueError:
        return None


__all__ = ["append_call_record", "build_call_record"]
