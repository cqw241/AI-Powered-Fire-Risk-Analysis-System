"""Qwen OpenAI-compatible client for structured image analysis."""

from __future__ import annotations

import base64
import json
from enum import StrEnum
from pathlib import Path
from typing import Any

from openai import AsyncOpenAI, OpenAIError
from pydantic import ValidationError

from fire_safety import PROJECT_ROOT
from fire_safety.image import PreparedImage
from fire_safety.schemas import VisualInvestigation, load_visual_investigation_schema
from fire_safety.settings import Settings, get_settings

PROMPT_PATH = PROJECT_ROOT / "prompts" / "visual_investigator.md"
ISSUE_CODES_PATH = PROJECT_ROOT / "data" / "legal" / "issue_codes.json"
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
    issue_codes_path: str | Path = ISSUE_CODES_PATH,
) -> str:
    """Load the visual prompt and inject the controlled Issue Code catalog."""

    try:
        template = Path(prompt_path).read_text(encoding="utf-8")
        catalog_data = json.loads(Path(issue_codes_path).read_text(encoding="utf-8"))
        issue_codes = catalog_data["issue_codes"]
        if not isinstance(issue_codes, list):
            raise TypeError("issue_codes must be an array")
        catalog_lines = []
        for item in issue_codes:
            code = item["code"]
            definition = item["definition"]
            if not isinstance(code, str) or not code.strip():
                raise TypeError("issue code must be a non-empty string")
            if not isinstance(definition, str) or not definition.strip():
                raise TypeError("issue definition must be a non-empty string")
            catalog_lines.append(f"- `{code}`：{definition}")
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
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

    qwen_client = client or AsyncOpenAI(
        base_url=app_settings.qwen_base_url,
        api_key=app_settings.qwen_api_key.get_secret_value(),
        max_retries=0,
    )
    prompt = build_visual_prompt()
    schema = load_visual_investigation_schema()
    data_url = _image_data_url(image)

    try:
        response = await qwen_client.chat.completions.create(
            model=app_settings.qwen_model,
            messages=[
                {"role": "system", "content": prompt},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "请分析这张消防场景图片。"},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                },
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "visual_investigation",
                    "strict": True,
                    "schema": schema,
                },
            },
        )
    except OpenAIError as exc:
        raise QwenRequestError(
            "Qwen 视觉分析请求失败",
            reason="request_failed",
        ) from exc

    content = _response_content(response)
    try:
        return VisualInvestigation.model_validate_json(content)
    except (ValidationError, ValueError) as exc:
        raise InvalidModelOutputError(
            "Qwen 返回的结构化结果无效",
            reason="schema_validation_failed",
        ) from exc


def _image_data_url(image: PreparedImage) -> str:
    payload = base64.b64encode(image.qwen_bytes).decode("ascii")
    return f"data:{image.media_type};base64,{payload}"


def _response_content(response: Any) -> str:
    try:
        content = response.choices[0].message.content
    except (AttributeError, IndexError, TypeError) as exc:
        return _missing_response_content(exc)
    if not isinstance(content, str) or not content.strip():
        return _missing_response_content()
    return content


def _missing_response_content(cause: Exception | None = None) -> str:
    error = InvalidModelOutputError(
        "Qwen 响应缺少结构化内容",
        reason="missing_response_content",
    )
    if cause is not None:
        raise error from cause
    raise error


__all__ = [
    "ISSUE_CODES_PATH",
    "PROMPT_PATH",
    "InvalidModelOutputError",
    "QwenConfigurationError",
    "QwenError",
    "QwenRequestError",
    "QwenStatus",
    "analyze_image",
    "build_visual_prompt",
]
