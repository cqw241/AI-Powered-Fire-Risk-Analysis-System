"""Pydantic models for structured visual fire-risk analysis."""

from __future__ import annotations

import json
from enum import StrEnum
from pathlib import Path
from typing import Annotated, TypeAlias

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    StringConstraints,
    field_validator,
)

from fire_safety import PROJECT_ROOT

VISUAL_SCHEMA_PATH = PROJECT_ROOT / "schemas" / "visual_investigation.schema.json"

NonEmptyStr: TypeAlias = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, strict=True),
]
NormalizedCoordinate: TypeAlias = Annotated[StrictInt, Field(ge=0, le=1000)]
NormalizedBBox: TypeAlias = Annotated[
    list[NormalizedCoordinate], Field(min_length=4, max_length=4)
]


class RiskPriority(StrEnum):
    """Allowed visual risk priority values."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class VisualModel(BaseModel):
    """Base configuration shared by all model-facing schemas."""

    model_config = ConfigDict(extra="forbid")


class VisualRegion(VisualModel):
    """A visible image region using the normalized 0-1000 coordinate space."""

    region_id: NonEmptyStr
    bbox_1000: NormalizedBBox
    label: NonEmptyStr

class Evidence(VisualModel):
    """Visible evidence supporting a finding."""

    text: NonEmptyStr
    region_ids: list[NonEmptyStr]

    @field_validator("region_ids")
    @classmethod
    def validate_unique_region_ids(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("region_ids must not contain duplicates")
        return value


class Finding(VisualModel):
    """One model-discovered fire-safety concern."""

    finding_id: NonEmptyStr
    title: NonEmptyStr
    description: NonEmptyStr
    risk_mechanism: NonEmptyStr
    risk_priority: RiskPriority
    evidence: Annotated[list[Evidence], Field(min_length=1)]
    suggested_issue_codes: list[NonEmptyStr]
    limitations: list[NonEmptyStr]

    @field_validator("suggested_issue_codes")
    @classmethod
    def validate_unique_issue_codes(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("suggested_issue_codes must not contain duplicates")
        return value


class VisualInvestigation(VisualModel):
    """Complete structured output returned by the visual investigator."""

    scene_summary: NonEmptyStr
    regions: list[VisualRegion]
    findings: list[Finding]


def load_visual_investigation_schema(
    path: str | Path = VISUAL_SCHEMA_PATH,
) -> dict[str, object]:
    """Load the checked-in JSON Schema sent to the model provider."""

    schema_path = Path(path)
    with schema_path.open(encoding="utf-8") as schema_file:
        schema = json.load(schema_file)
    if not isinstance(schema, dict):
        raise ValueError("visual investigation schema must be a JSON object")
    return schema


__all__ = [
    "Evidence",
    "Finding",
    "NormalizedBBox",
    "RiskPriority",
    "VISUAL_SCHEMA_PATH",
    "VisualInvestigation",
    "VisualRegion",
    "load_visual_investigation_schema",
]
