"""Qwen OpenAI-compatible client for structured image analysis."""

from __future__ import annotations

import base64
import json
from collections.abc import AsyncIterator
from enum import StrEnum
from pathlib import Path
from time import perf_counter
from typing import Any

from openai import AsyncOpenAI, OpenAIError
from pydantic import ValidationError

from fire_safety import PROJECT_ROOT
from fire_safety.image import PreparedImage
from fire_safety.risk_packs import (
    RuleCatalog,
    RuleDataError,
    get_rule_catalog,
    load_issue_code_definitions,
)
from fire_safety.schemas import VisualInvestigation, load_visual_investigation_schema
from fire_safety.settings import Settings, get_settings
from fire_safety.timing import POST_MODEL_STAGE, note, stage

PROMPT_PATH = PROJECT_ROOT / "prompts" / "visual_investigator.md"
ISSUE_CATALOG_PLACEHOLDER = "{{ISSUE_CATALOG}}"


class QwenStatus(StrEnum):
    """Pipeline-facing status values produced by the model stage."""

    MODEL_FAILED = "model_failed"
    INVALID_MODEL_OUTPUT = "invalid_model_output"


class QwenError(RuntimeError):
    """Base error for failures in the model stage.

    ``status`` is defined here, not only on the subclasses, so the pipeline's
    ``except QwenError`` handler can always map a model-stage failure to a
    public status instead of raising ``AttributeError`` out of the handler.
    """

    status = QwenStatus.MODEL_FAILED

    def __init__(self, message: str, *, reason: str):
        super().__init__(message)
        self.reason = reason


class QwenConfigurationError(QwenError):
    """Raised before a request when required local configuration is missing."""

    status = QwenStatus.MODEL_FAILED


class QwenRequestError(QwenError):
    """Raised when the provider request fails."""

    status = QwenStatus.MODEL_FAILED


class InvalidModelOutputError(QwenError):
    """Raised when a provider response cannot satisfy the visual schema."""

    status = QwenStatus.INVALID_MODEL_OUTPUT


def build_visual_prompt(
    prompt_path: str | Path = PROMPT_PATH,
    issue_codes_path: str | Path | None = None,
    *,
    rule_catalog: RuleCatalog | None = None,
) -> str:
    """Load the visual prompt and inject the unified controlled Issue Code catalog."""

    try:
        template = Path(prompt_path).read_text(encoding="utf-8")
        if issue_codes_path is not None and rule_catalog is not None:
            raise RuleDataError("issue_codes_path 和 rule_catalog 不能同时提供")
        definitions = (
            load_issue_code_definitions(issue_codes_path)
            if issue_codes_path is not None
            else (rule_catalog or get_rule_catalog()).issue_codes
        )
        catalog_lines = [f"- `{item.code}`：{item.definition}" for item in definitions]
    except (OSError, RuleDataError) as exc:
        raise QwenConfigurationError(
            "视觉分析 Prompt 或 Issue Code 目录无法加载",
            reason="invalid_prompt_resources",
        ) from exc

    if template.count(ISSUE_CATALOG_PLACEHOLDER) != 1:
        raise QwenConfigurationError(
            "视觉分析 Prompt 必须包含一个 Issue Code 目录占位符",
            reason="invalid_prompt_template",
        )
    return template.replace(ISSUE_CATALOG_PLACEHOLDER, "\n".join(catalog_lines))


async def analyze_image(
    image: PreparedImage,
    settings: Settings | None = None,
    client: AsyncOpenAI | None = None,
) -> VisualInvestigation:
    """Analyze one prepared image using exactly one model completion request."""

    app_settings = settings or get_settings()
    if not app_settings.qwen_configured:
        raise QwenConfigurationError(
            "Qwen 服务配置不完整",
            reason="missing_qwen_configuration",
        )

    # Setup and request share one stage name: the recorder merges the adjacent
    # records into a single 模型请求 row. Setup is not free — the client is
    # built from scratch (no connection pool reuse) and the prompt, schema, and
    # base64 image payload are all re-read or re-encoded on every call.
    with stage("模型请求"):
        qwen_client = client or AsyncOpenAI(
            base_url=app_settings.qwen_base_url,
            api_key=app_settings.qwen_api_key.get_secret_value(),
            max_retries=0,
        )
        prompt = build_visual_prompt()
        schema = load_visual_investigation_schema()
        data_url = _image_data_url(image)
    image_content: dict[str, Any] = {
        "type": "image_url",
        "image_url": {"url": data_url},
    }
    extra_body: dict[str, Any] = {}
    if app_settings.qwen_provider == "dashscope":
        image_content["max_pixels"] = app_settings.qwen_max_pixels
    else:
        extra_body["mm_processor_kwargs"] = {
            "max_pixels": app_settings.qwen_max_pixels,
        }

    request_kwargs: dict[str, Any] = {
        "model": app_settings.qwen_model,
        "messages": [
            {"role": "system", "content": prompt},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "请分析这张消防场景图片。"},
                    image_content,
                ],
            },
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "visual_investigation",
                "strict": True,
                "schema": schema,
            },
        },
    }
    if app_settings.qwen_reasoning_effort is not None:
        extra_body["reasoning_effort"] = app_settings.qwen_reasoning_effort
    if extra_body:
        request_kwargs["extra_body"] = extra_body
    # Streamed so the client can tell waiting from generating: a single
    # non-streamed await only ever yields one number for both. include_usage
    # makes the endpoint report token accounting on the final chunk.
    request_kwargs["stream"] = True
    request_kwargs["stream_options"] = {"include_usage": True}

    with stage("模型请求"):
        requested_at = perf_counter()
        try:
            stream = await qwen_client.chat.completions.create(**request_kwargs)
            content, usage, first_frame_seconds, first_content_seconds = await _read_stream(
                stream, requested_at=requested_at
            )
        except OpenAIError as exc:
            raise QwenRequestError(
                "Qwen 视觉分析请求失败",
                reason="request_failed",
            ) from exc
    _record_request_notes(usage, first_frame_seconds, first_content_seconds)

    with stage(POST_MODEL_STAGE):
        try:
            payload = json.loads(_require_content(content))
        except json.JSONDecodeError as exc:
            raise InvalidModelOutputError(
                "Qwen 返回的结构化结果无效",
                reason="schema_validation_failed",
            ) from exc
        # DashScope OpenAI-compatible mode has been observed wrapping the single
        # structured object in a one-element array; exactly-one-element arrays are
        # normalized, anything else reaches the validator and is reported as-is.
        if isinstance(payload, list) and len(payload) == 1 and isinstance(payload[0], dict):
            payload = payload[0]
        try:
            return VisualInvestigation.model_validate(payload)
        except (ValidationError, ValueError) as exc:
            raise InvalidModelOutputError(
                "Qwen 返回的结构化结果无效",
                reason="schema_validation_failed",
            ) from exc


def _image_data_url(image: PreparedImage) -> str:
    payload = base64.b64encode(image.qwen_bytes).decode("ascii")
    return f"data:{image.media_type};base64,{payload}"


async def _read_stream(
    stream: AsyncIterator[Any], *, requested_at: float
) -> tuple[str, Any, float | None, float | None]:
    """Reassemble a streamed completion and mark where its wait ended.

    Returns the joined content, the provider's usage object when the endpoint
    reports one, and two timings measured from the request:

    - ``ttfb``: the first generation frame. Everything before it is connect,
      upload, server queueing, image encoding, and prompt prefill;
    - ``ttft``: the first frame carrying visible content. A reasoning model
      emits its thinking here first, so ``ttft - ttfb`` is thinking time.

    The gap between the two is what a non-streamed request cannot show: one
    await collapses waiting, thinking, and answering into a single number.
    """

    parts: list[str] = []
    usage: Any = None
    first_frame_seconds: float | None = None
    first_content_seconds: float | None = None
    async for chunk in stream:
        if getattr(chunk, "usage", None) is not None:
            usage = chunk.usage
        choices = getattr(chunk, "choices", None) or []
        if choices and first_frame_seconds is None:
            first_frame_seconds = perf_counter() - requested_at
        for choice in choices:
            text = getattr(getattr(choice, "delta", None), "content", None)
            if not text:
                continue
            if first_content_seconds is None:
                first_content_seconds = perf_counter() - requested_at
            parts.append(text)
    return "".join(parts), usage, first_frame_seconds, first_content_seconds


def _record_request_notes(
    usage: Any, first_frame_seconds: float | None, first_content_seconds: float | None
) -> None:
    """Record where the model request spent its time and its tokens."""

    if first_frame_seconds is not None:
        note("ttfb", f"{first_frame_seconds:.3f}s")
    if first_content_seconds is not None:
        note("ttft", f"{first_content_seconds:.3f}s")
    if usage is None:
        return
    for name, value in (
        ("prompt_tokens", getattr(usage, "prompt_tokens", None)),
        ("completion_tokens", getattr(usage, "completion_tokens", None)),
    ):
        if value is not None:
            note(name, value)
    reasoning = getattr(
        getattr(usage, "completion_tokens_details", None), "reasoning_tokens", None
    )
    if reasoning is not None:
        note("reasoning_tokens", reasoning)


def _require_content(content: str) -> str:
    """Reject an empty streamed response with the documented reason."""

    if not content.strip():
        raise InvalidModelOutputError(
            "Qwen 响应缺少结构化内容",
            reason="missing_response_content",
        )
    return content


__all__ = [
    "PROMPT_PATH",
    "InvalidModelOutputError",
    "QwenConfigurationError",
    "QwenError",
    "QwenRequestError",
    "QwenStatus",
    "analyze_image",
    "build_visual_prompt",
]
