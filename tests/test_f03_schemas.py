from __future__ import annotations

import json
from typing import Any

import pytest
from pydantic import ValidationError

from fire_safety.schemas import VisualInvestigation, load_visual_investigation_schema


def test_valid_visual_investigation_is_parsed(valid_visual_output: dict[str, Any]) -> None:
    investigation = VisualInvestigation.model_validate(valid_visual_output)

    assert investigation.scene_summary.startswith("室内通行区域")
    assert investigation.regions[0].bbox_1000 == [120, 410, 810, 950]
    assert investigation.findings[0].risk_priority == "high"


def test_scene_level_evidence_can_have_no_regions(
    valid_visual_output: dict[str, Any],
) -> None:
    valid_visual_output["findings"][0]["evidence"][0]["region_ids"] = []

    investigation = VisualInvestigation.model_validate(valid_visual_output)

    assert investigation.findings[0].evidence[0].region_ids == []


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("scene_summary", ""),
        ("scene_summary", "   "),
        ("scene_summary", 123),
    ],
)
def test_scene_summary_must_be_a_non_empty_string(
    field: str, value: object, valid_visual_output: dict[str, Any]
) -> None:
    valid_visual_output[field] = value

    with pytest.raises(ValidationError):
        VisualInvestigation.model_validate(valid_visual_output)


@pytest.mark.parametrize(
    "bbox",
    [
        [-1, 0, 10, 10],
        [0, 0, 1001, 10],
        [0, 0, 1.0, 2],
        [False, 0, 10, 20],
        [0, 0, 10],
    ],
)
def test_structurally_invalid_bbox_is_rejected(
    bbox: list[object], valid_visual_output: dict[str, Any]
) -> None:
    valid_visual_output["regions"][0]["bbox_1000"] = bbox

    with pytest.raises(ValidationError):
        VisualInvestigation.model_validate(valid_visual_output)


@pytest.mark.parametrize("bbox", [[10, 10, 10, 20], [20, 0, 10, 20]])
def test_semantically_invalid_bbox_is_deferred_to_visual_cleanup(
    bbox: list[int], valid_visual_output: dict[str, Any]
) -> None:
    valid_visual_output["regions"][0]["bbox_1000"] = bbox

    investigation = VisualInvestigation.model_validate(valid_visual_output)

    assert investigation.regions[0].bbox_1000 == bbox


def test_finding_requires_evidence(valid_visual_output: dict[str, Any]) -> None:
    valid_visual_output["findings"][0]["evidence"] = []

    with pytest.raises(ValidationError):
        VisualInvestigation.model_validate(valid_visual_output)


def test_invalid_priority_and_extra_fields_are_rejected(
    valid_visual_output: dict[str, Any],
) -> None:
    bad_priority = valid_visual_output
    bad_priority["findings"][0]["risk_priority"] = "urgent"
    with pytest.raises(ValidationError):
        VisualInvestigation.model_validate(bad_priority)

    bad_priority["findings"][0]["risk_priority"] = "high"
    extra_field = bad_priority
    extra_field["regions"][0]["confidence"] = 0.9
    with pytest.raises(ValidationError):
        VisualInvestigation.model_validate(extra_field)


@pytest.mark.parametrize("list_field", ["region_ids", "suggested_issue_codes"])
def test_schema_unique_lists_reject_duplicates(
    list_field: str, valid_visual_output: dict[str, Any]
) -> None:
    finding = valid_visual_output["findings"][0]
    if list_field == "region_ids":
        finding["evidence"][0][list_field] = ["R1", "R1"]
    else:
        finding[list_field] = ["PASSAGE_OBSTRUCTED", "PASSAGE_OBSTRUCTED"]

    with pytest.raises(ValidationError):
        VisualInvestigation.model_validate(valid_visual_output)


def test_checked_in_json_schema_matches_visual_contract() -> None:
    schema = load_visual_investigation_schema()

    assert schema["title"] == "VisualInvestigation"
    assert "$schema" not in schema
    assert "$id" not in schema
    assert "uniqueItems" not in json.dumps(schema)
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {"scene_summary", "regions", "findings"}
    assert set(schema["$defs"]) == {"bbox", "region", "evidence", "finding"}
    assert schema["$defs"]["finding"]["properties"]["risk_priority"]["enum"] == [
        "high",
        "medium",
        "low",
    ]
