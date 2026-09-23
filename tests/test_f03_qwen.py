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
from pydantic import ValidationError

from fire_safety.image import PreparedImage, prepare_image
from fire_safety.qwen import (
    InvalidModelOutputError,
    QwenConfigurationError,
    QwenRequestError,
    QwenStatus,
    analyze_image,
    build_visual_prompt,
)
from fire_safety.risk_packs import IssueCodeDefinition, RuleCatalog
from fire_safety.schemas import VisualInvestigation, load_visual_investigation_schema
from fire_safety.settings import Settings


def prepared_image() -> PreparedImage:
    output = BytesIO()
    Image.new("RGB", (20, 10), color=(30, 60, 90)).save(output, format="PNG")
    return prepare_image(output.getvalue())


def valid_response_json() -> str:
    return json.dumps(VALID_VISUAL_OUTPUT, ensure_ascii=False)


DEFAULT_RESPONSE = object()


class FakeChunkStream:
    """Async chunk stream: content pieces first, then one usage-only frame."""

    def __init__(self, contents: list[str], usage: Any = None):
        self._contents = contents
        self._usage = usage

    def __aiter__(self) -> Any:
        return self._iterate()

    async def _iterate(self) -> Any:
        for text in self._contents:
            yield SimpleNamespace(
                choices=[SimpleNamespace(delta=SimpleNamespace(content=text))],
                usage=None,
            )
        if self._usage is not None:
            yield SimpleNamespace(choices=[], usage=self._usage)


class FakeCompletions:
    def __init__(
        self,
        *,
        content: Any = DEFAULT_RESPONSE,
        error: Exception | None = None,
        usage: Any = None,
    ):
        self.content = valid_response_json() if content is DEFAULT_RESPONSE else content
        self.error = error
        self.usage = usage
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return FakeChunkStream(self._chunks(), self.usage)

    def _chunks(self) -> list[str]:
        """Split the payload in two so tests exercise stream reassembly."""

        if not isinstance(self.content, str) or not self.content:
            return []
        middle = len(self.content) // 2
        return [self.content[:middle], self.content[middle:]]


class FakeClient:
    def __init__(self, completions: FakeCompletions):
        self.chat = SimpleNamespace(completions=completions)


def configured_settings() -> Settings:
    return Settings(
        qwen_base_url="https://qwen.example/v1",
        qwen_api_key="test-key",
        qwen_model="qwen-test-model",
        qwen_provider="dashscope",
        qwen_max_pixels=4_194_304,
        qwen_reasoning_effort="low",
        qwen_temperature=0.1,
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
    assert "禁止输出" in prompt
    assert "法规名称" in prompt


def test_prompt_uses_the_injected_rule_catalog() -> None:
    catalog = RuleCatalog(
        schema_version="1.0",
        catalog_id="prompt-test",
        issue_codes=(
            IssueCodeDefinition(
                code="CUSTOM_PROMPT_CODE",
                display_name="自定义问题",
                definition="只应来自传入的统一 RuleCatalog",
                default_priority="medium",
                default_action="现场处理",
            ),
        ),
        bindings=(),
        clauses=(),
    )

    prompt = build_visual_prompt(rule_catalog=catalog)

    assert "`CUSTOM_PROMPT_CODE`：只应来自传入的统一 RuleCatalog" in prompt
    assert "`PASSAGE_OBSTRUCTED`" not in prompt


def test_analyze_image_sends_one_strict_structured_request() -> None:
    completions = FakeCompletions()
    image = prepared_image()
    client = cast(AsyncOpenAI, FakeClient(completions))

    result = asyncio.run(analyze_image(image, configured_settings(), client))

    assert result.findings[0].finding_id == "F1"
    assert len(completions.calls) == 1
    request = completions.calls[0]
    assert request["model"] == "qwen-test-model"
    assert request["temperature"] == 0.1
    assert request["extra_body"] == {"reasoning_effort": "low"}
    assert request["response_format"] == {
        "type": "json_schema",
        "json_schema": {
            "name": "visual_investigation",
            "strict": True,
            "schema": load_visual_investigation_schema(),
        },
    }
    assert request["stream"] is True
    assert request["stream_options"] == {"include_usage": True}
    assert request["messages"][0]["role"] == "system"
    image_content = request["messages"][1]["content"][1]
    assert image_content["max_pixels"] == 4_194_304
    image_url = image_content["image_url"]["url"]
    prefix, encoded = image_url.split(",", maxsplit=1)
    assert prefix == "data:image/png;base64"
    assert base64.b64decode(encoded) == image.qwen_bytes


def test_default_dashscope_request_sends_default_pixels_without_reasoning() -> None:
    completions = FakeCompletions()
    settings = Settings(
        qwen_base_url="https://qwen.example/v1",
        qwen_api_key="test-key",
        qwen_model="qwen-test-model",
        _env_file=None,
    )

    run_analysis(completions, settings=settings)

    assert "extra_body" not in completions.calls[0]
    assert "temperature" not in completions.calls[0]
    image_content = completions.calls[0]["messages"][1]["content"][1]
    assert image_content["max_pixels"] == 8_388_608


def test_invalid_reasoning_effort_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(qwen_reasoning_effort="extreme", _env_file=None)


@pytest.mark.parametrize("temperature", [-0.1, 2.1])
def test_out_of_range_temperature_is_rejected(temperature: float) -> None:
    with pytest.raises(ValidationError):
        Settings(qwen_temperature=temperature, _env_file=None)


@pytest.mark.parametrize("reasoning_effort", ["none", "low", "medium", "xhigh"])
def test_supported_reasoning_effort_is_accepted(reasoning_effort: str) -> None:
    settings = Settings(qwen_reasoning_effort=reasoning_effort, _env_file=None)

    assert settings.qwen_reasoning_effort == reasoning_effort


def test_invalid_qwen_provider_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(qwen_provider="other", _env_file=None)


@pytest.mark.parametrize("max_pixels", [-1, 0, 65_535, 16_777_217])
def test_out_of_range_qwen_max_pixels_is_rejected(max_pixels: int) -> None:
    with pytest.raises(ValidationError):
        Settings(qwen_max_pixels=max_pixels, _env_file=None)


@pytest.mark.parametrize("max_pixels", [65_536, 16_777_216])
def test_qwen_max_pixels_accepts_documented_boundaries(max_pixels: int) -> None:
    assert Settings(qwen_max_pixels=max_pixels, _env_file=None).qwen_max_pixels == max_pixels


def test_vllm_sends_max_pixels_as_processor_kwargs() -> None:
    completions = FakeCompletions()
    settings = Settings(
        qwen_base_url="https://qwen.example/v1",
        qwen_api_key="test-key",
        qwen_model="qwen-test-model",
        qwen_provider="vllm",
        qwen_max_pixels=4_194_304,
        qwen_reasoning_effort="low",
        _env_file=None,
    )

    run_analysis(completions, settings=settings)

    request = completions.calls[0]
    assert request["extra_body"] == {
        "mm_processor_kwargs": {"max_pixels": 4_194_304},
        "reasoning_effort": "low",
    }
    assert "max_pixels" not in request["messages"][1]["content"][1]


def test_llamacpp_omits_unsupported_pixel_fields() -> None:
    completions = FakeCompletions()
    settings = Settings(
        qwen_base_url="http://127.0.0.1:8092/v1",
        qwen_api_key="local",
        qwen_model="Qwen3.8-27B",
        qwen_provider="llamacpp",
        qwen_max_pixels=4_194_304,
        qwen_reasoning_effort="low",
        _env_file=None,
    )

    run_analysis(completions, settings=settings)

    request = completions.calls[0]
    assert request["extra_body"] == {"reasoning_effort": "low"}
    assert "max_pixels" not in request["messages"][1]["content"][1]


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


def test_array_wrapped_valid_content_is_unwrapped() -> None:
    wrapped = f"[{valid_response_json()}]"

    investigation = run_analysis(FakeCompletions(content=wrapped))

    assert investigation.findings[0].finding_id == "F1"
    assert investigation.scene_summary == VALID_VISUAL_OUTPUT["scene_summary"]


@pytest.mark.parametrize("content", ["", None, []])
def test_missing_response_content_maps_to_invalid_model_output(content: Any) -> None:
    completions = FakeCompletions(content=content)

    with pytest.raises(InvalidModelOutputError) as error:
        run_analysis(completions)

    assert error.value.status is QwenStatus.INVALID_MODEL_OUTPUT
    assert error.value.reason == "missing_response_content"
