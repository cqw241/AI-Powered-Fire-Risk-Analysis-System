from __future__ import annotations

import asyncio
import json
from io import BytesIO
from typing import Any

import jsonschema
import pytest
from conftest import VALID_VISUAL_OUTPUT
from PIL import Image
from pydantic import ValidationError

from fire_safety import PROJECT_ROOT
from fire_safety.image import PreparedImage, prepare_image
from fire_safety.pipeline import analyze
from fire_safety.qwen import InvalidModelOutputError, QwenError, QwenRequestError
from fire_safety.rules import RuleDataError, load_rule_catalog
from fire_safety.schemas import (
    AnalysisFinding,
    AnalysisStatus,
    VisualInvestigation,
    load_analysis_result_schema,
)


def png_bytes() -> bytes:
    output = BytesIO()
    Image.new("RGB", (100, 100), color=(30, 60, 90)).save(output, format="PNG")
    return output.getvalue()


def prepared_image():
    return prepare_image(png_bytes())


def run(coro):
    return asyncio.run(coro)


def visual_output(payload: dict[str, Any] | None = None) -> VisualInvestigation:
    return VisualInvestigation.model_validate(payload or VALID_VISUAL_OUTPUT)


def test_analyze_composes_findings_and_legal_associations() -> None:
    async def fake_qwen(image, settings=None):
        return visual_output()

    result = run(
        analyze(
            prepared_image(),
            qwen_analyzer=fake_qwen,
            rule_catalog=load_rule_catalog(),
        )
    )

    assert result.status is AnalysisStatus.COMPLETED
    assert len(result.findings) == 1
    finding = result.findings[0]
    assert finding.finding_id == "F1"
    assert finding.rule_status.value == "matched"
    assert finding.legal_associations
    assert finding.legal_associations[0].relation.value == "conditional"
    assert finding.legal_associations[0].missing_conditions
    assert finding.evidence[0].bboxes == [[120, 410, 810, 950]]


def test_analyze_cleans_invalid_regions_but_keeps_finding() -> None:
    payload = {
        **VALID_VISUAL_OUTPUT,
        "regions": [
            VALID_VISUAL_OUTPUT["regions"][0],
            {"region_id": "R2", "bbox_1000": [500, 500, 500, 700], "label": "无效区域"},
        ],
        "findings": [
            {
                **VALID_VISUAL_OUTPUT["findings"][0],
                "evidence": [
                    {
                        "text": "纸箱和无效区域",
                        "region_ids": ["R1", "R2", "MISSING"],
                    }
                ],
            }
        ],
    }

    async def fake_qwen(image, settings=None):
        return visual_output(payload)

    result = run(
        analyze(
            prepared_image(),
            qwen_analyzer=fake_qwen,
            rule_catalog=load_rule_catalog(),
        )
    )

    assert result.status is AnalysisStatus.COMPLETED
    assert len(result.findings) == 1
    assert result.findings[0].evidence[0].bboxes == [[120, 410, 810, 950]]


def test_unknown_issue_code_does_not_drop_finding() -> None:
    payload = {
        **VALID_VISUAL_OUTPUT,
        "findings": [
            {**VALID_VISUAL_OUTPUT["findings"][0], "suggested_issue_codes": ["UNKNOWN"]}
        ],
    }

    async def fake_qwen(image, settings=None):
        return visual_output(payload)

    result = run(
        analyze(
            prepared_image(),
            qwen_analyzer=fake_qwen,
            rule_catalog=load_rule_catalog(),
        )
    )

    assert result.status is AnalysisStatus.COMPLETED
    assert len(result.findings) == 1
    assert result.findings[0].legal_associations == []
    assert result.findings[0].rule_status.value == "no_valid_issue_code"


def test_no_findings_maps_to_no_findings() -> None:
    payload = {**VALID_VISUAL_OUTPUT, "findings": []}

    async def fake_qwen(image, settings=None):
        return visual_output(payload)

    result = run(analyze(prepared_image(), qwen_analyzer=fake_qwen))

    assert result.status is AnalysisStatus.NO_FINDINGS
    assert result.findings == []
    assert result.message


def test_image_failure_maps_to_image_unusable() -> None:
    result = run(analyze(b"not an image"))

    assert result.status is AnalysisStatus.IMAGE_UNUSABLE
    assert result.findings == []
    assert result.message


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (QwenRequestError("request failed", reason="request_failed"), AnalysisStatus.MODEL_FAILED),
        (
            InvalidModelOutputError("invalid output", reason="schema_validation_failed"),
            AnalysisStatus.INVALID_MODEL_OUTPUT,
        ),
    ],
)
def test_model_failures_map_to_public_status(error, status) -> None:
    async def fake_qwen(image, settings=None):
        raise error

    result = run(analyze(prepared_image(), qwen_analyzer=fake_qwen))

    assert result.status is status
    assert result.findings == []
    assert result.message


def test_invalid_visual_return_maps_to_invalid_model_output() -> None:
    async def fake_qwen(image, settings=None):
        return {"not": "a visual investigation"}

    result = run(analyze(prepared_image(), qwen_analyzer=fake_qwen))

    assert result.status is AnalysisStatus.INVALID_MODEL_OUTPUT
    assert result.findings == []


def test_duplicate_finding_ids_map_to_invalid_model_output() -> None:
    finding = VALID_VISUAL_OUTPUT["findings"][0]
    payload = {**VALID_VISUAL_OUTPUT, "findings": [finding, {**finding, "title": "重复风险"}]}

    async def fake_qwen(image, settings=None):
        return visual_output(payload)

    result = run(analyze(prepared_image(), qwen_analyzer=fake_qwen))

    assert result.status is AnalysisStatus.INVALID_MODEL_OUTPUT


def test_raw_upload_is_prepared_before_reaching_the_model() -> None:
    """The success path must exercise prepare_image, not only accept a PreparedImage."""

    received: list[Any] = []

    async def fake_qwen(image, settings=None):
        received.append(image)
        return visual_output()

    result = run(
        analyze(png_bytes(), qwen_analyzer=fake_qwen, rule_catalog=load_rule_catalog())
    )

    assert result.status is AnalysisStatus.COMPLETED
    assert isinstance(received[0], PreparedImage)
    assert (received[0].width, received[0].height) == (100, 100)
    assert result.findings[0].evidence[0].bboxes == [[120, 410, 810, 950]]


def test_duplicate_region_ids_are_dropped_but_the_finding_survives() -> None:
    payload = {
        **VALID_VISUAL_OUTPUT,
        "regions": [
            VALID_VISUAL_OUTPUT["regions"][0],
            {"region_id": "R1", "bbox_1000": [10, 10, 20, 20], "label": "同名区域"},
        ],
    }

    async def fake_qwen(image, settings=None):
        return visual_output(payload)

    result = run(
        analyze(prepared_image(), qwen_analyzer=fake_qwen, rule_catalog=load_rule_catalog())
    )

    assert result.status is AnalysisStatus.COMPLETED
    assert len(result.findings) == 1
    # Both copies are ambiguous, so neither is guessed at; the Finding stays.
    assert result.findings[0].evidence[0].bboxes == []


def test_broken_rule_package_is_caught_before_the_model_is_called(monkeypatch) -> None:
    calls: list[Any] = []

    async def fake_qwen(image, settings=None):
        calls.append(image)
        return visual_output()

    def broken_catalog():
        raise RuleDataError("无法读取法规规则文件: clauses.json")

    monkeypatch.setattr("fire_safety.pipeline.get_rule_catalog", broken_catalog)

    result = run(analyze(prepared_image(), qwen_analyzer=fake_qwen))

    assert result.status is AnalysisStatus.MODEL_FAILED
    assert result.message
    assert calls == []


def test_bare_qwen_error_still_maps_to_a_public_status() -> None:
    async def fake_qwen(image, settings=None):
        raise QwenError("模型阶段失败", reason="unclassified")

    result = run(analyze(prepared_image(), qwen_analyzer=fake_qwen))

    assert result.status is AnalysisStatus.MODEL_FAILED


def test_analysis_finding_requires_at_least_one_evidence_item() -> None:
    with pytest.raises(ValidationError):
        AnalysisFinding(
            finding_id="F1",
            title="标题",
            description="描述",
            risk_priority="high",
            risk_mechanism="机理",
            evidence=[],
            legal_associations=[],
            limitations=[],
            recommended_action="建议",
            rule_status="no_valid_issue_code",
            rule_warnings=[],
        )


def test_completed_payload_matches_the_checked_in_schema() -> None:
    async def fake_qwen(image, settings=None):
        return visual_output()

    result = run(
        analyze(prepared_image(), qwen_analyzer=fake_qwen, rule_catalog=load_rule_catalog())
    )
    payload = result.to_payload()

    # `null` is not a valid `message`: the schema types it as a string.
    assert "message" not in payload
    jsonschema.validate(payload, load_analysis_result_schema())


@pytest.mark.parametrize(
    "make_result",
    [
        lambda: run(analyze(b"not an image")),
        lambda: run(analyze(prepared_image(), qwen_analyzer=_raise(QwenRequestError))),
        lambda: run(analyze(prepared_image(), qwen_analyzer=_raise(InvalidModelOutputError))),
        lambda: run(analyze(prepared_image(), qwen_analyzer=_return({"not": "visual"}))),
        lambda: run(analyze(prepared_image(), qwen_analyzer=_return_no_findings())),
    ],
    ids=["image_unusable", "model_failed", "invalid_model_output", "invalid_json", "no_findings"],
)
def test_non_completed_payloads_match_the_checked_in_schema(make_result) -> None:
    jsonschema.validate(make_result().to_payload(), load_analysis_result_schema())


def test_example_result_matches_the_checked_in_schema() -> None:
    example_path = PROJECT_ROOT / "examples" / "analysis_result.example.json"
    example = json.loads(example_path.read_text(encoding="utf-8"))

    jsonschema.validate(example, load_analysis_result_schema())


def _raise(error_type):
    async def fake_qwen(image, settings=None):
        raise error_type("模型阶段失败", reason="test")

    return fake_qwen


def _return(payload):
    async def fake_qwen(image, settings=None):
        return payload

    return fake_qwen


def _return_no_findings():
    return _return(visual_output({**VALID_VISUAL_OUTPUT, "findings": []}))
