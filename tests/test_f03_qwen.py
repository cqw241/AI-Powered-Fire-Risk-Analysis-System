from __future__ import annotations

import asyncio
import base64
import json
from io import BytesIO
from types import SimpleNamespace
from typing import Any, cast

import httpx
import pytest
from conftest import VALID_VISUAL_OUTPUT
from openai import APIConnectionError, AsyncOpenAI
from PIL import Image

from fire_safety.image import PreparedImage, prepare_image
from fire_safety.qwen import (
    InvalidModelOutputError,
    QwenConfigurationError,
    QwenRequestError,
    QwenStatus,
    analyze_image,
    build_visual_prompt,
)
from fire_safety.schemas import VisualInvestigation, load_visual_investigation_schema
from fire_safety.settings import Settings


def prepared_image() -> PreparedImage:
    output = BytesIO()
    Image.new("RGB", (20, 10), color=(30, 60, 90)).save(output, format="PNG")
    return prepare_image(output.getvalue())


def valid_response_json() -> str:
    return json.dumps(VALID_VISUAL_OUTPUT, ensure_ascii=False)


DEFAULT_RESPONSE = object()


class FakeCompletions:
    def __init__(
        self,
        *,
        content: Any = DEFAULT_RESPONSE,
        error: Exception | None = None,
    ):
        self.content = valid_response_json() if content is DEFAULT_RESPONSE else content
        self.error = error
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=self.content))]
        )


class FakeClient:
    def __init__(self, completions: FakeCompletions):
        self.chat = SimpleNamespace(completions=completions)


def configured_settings() -> Settings:
    return Settings(
        qwen_base_url="https://qwen.example/v1",
        qwen_api_key="test-key",
        qwen_model="qwen-test-model",
    )


def run_analysis(
    completions: FakeCompletions,
    *,
    settings: Settings | None = None,
) -> VisualInvestigation:
    client = cast(AsyncOpenAI, FakeClient(completions))
    return asyncio.run(analyze_image(prepared_image(), settings or configured_settings(), client))


def test_prompt_injects_issue_code_catalog() -> None:
    prompt = build_visual_prompt()

    assert "{{ISSUE_CATALOG}}" not in prompt
    assert "`PASSAGE_OBSTRUCTED`" in prompt
    assert "画面中的人员通行路径被物体明显占用" in prompt
    assert "不要输出" in prompt
    assert "法规名称" in prompt


def test_analyze_image_sends_one_strict_structured_request() -> None:
    completions = FakeCompletions()
    image = prepared_image()
    client = cast(AsyncOpenAI, FakeClient(completions))

    result = asyncio.run(analyze_image(image, configured_settings(), client))

    assert result.findings[0].finding_id == "F1"
    assert len(completions.calls) == 1
    request = completions.calls[0]
    assert request["model"] == "qwen-test-model"
    assert request["response_format"] == {
        "type": "json_schema",
        "json_schema": {
            "name": "visual_investigation",
            "strict": True,
            "schema": load_visual_investigation_schema(),
        },
    }
    assert request["messages"][0]["role"] == "system"
    image_url = request["messages"][1]["content"][1]["image_url"]["url"]
    prefix, encoded = image_url.split(",", maxsplit=1)
    assert prefix == "data:image/png;base64"
    assert base64.b64decode(encoded) == image.qwen_bytes


def test_missing_configuration_fails_before_request() -> None:
    completions = FakeCompletions()
    client = cast(AsyncOpenAI, FakeClient(completions))

    with pytest.raises(QwenConfigurationError) as error:
        asyncio.run(
            analyze_image(
                prepared_image(),
                Settings(qwen_base_url=None, qwen_api_key=None, _env_file=None),
                client,
            )
        )

    assert error.value.status is QwenStatus.MODEL_FAILED
    assert error.value.reason == "missing_qwen_configuration"
    assert completions.calls == []


def test_provider_error_maps_to_model_failed() -> None:
    request = httpx.Request("POST", "https://qwen.example/v1/chat/completions")
    completions = FakeCompletions(error=APIConnectionError(request=request))

    with pytest.raises(QwenRequestError) as error:
        run_analysis(completions)

    assert error.value.status is QwenStatus.MODEL_FAILED
    assert error.value.reason == "request_failed"
    assert len(completions.calls) == 1


@pytest.mark.parametrize(
    "content",
    [
        "not json",
        json.dumps({"scene_summary": "场景", "regions": []}),
        valid_response_json().replace("[120, 410, 810, 950]", "[120, 410, 1001, 950]"),
    ],
)
def test_invalid_structured_content_maps_to_invalid_model_output(content: str) -> None:
    with pytest.raises(InvalidModelOutputError) as error:
        run_analysis(FakeCompletions(content=content))

    assert error.value.status is QwenStatus.INVALID_MODEL_OUTPUT
    assert error.value.reason == "schema_validation_failed"


@pytest.mark.parametrize("content", ["", None, []])
def test_missing_response_content_maps_to_invalid_model_output(content: Any) -> None:
    completions = FakeCompletions(content=content)

    with pytest.raises(InvalidModelOutputError) as error:
        run_analysis(completions)

    assert error.value.status is QwenStatus.INVALID_MODEL_OUTPUT
    assert error.value.reason == "missing_response_content"
