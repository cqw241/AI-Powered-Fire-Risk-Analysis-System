from __future__ import annotations

import asyncio
from io import BytesIO
from typing import Any

import pytest
from conftest import VALID_VISUAL_OUTPUT
from PIL import Image

from fire_safety.image import prepare_image
from fire_safety.pipeline import analyze
from fire_safety.qwen import InvalidModelOutputError, QwenRequestError
from fire_safety.rules import load_rule_catalog
from fire_safety.schemas import AnalysisStatus, VisualInvestigation


def prepared_image():
    output = BytesIO()
    Image.new("RGB", (100, 100), color=(30, 60, 90)).save(output, format="PNG")
    return prepare_image(output.getvalue())


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
