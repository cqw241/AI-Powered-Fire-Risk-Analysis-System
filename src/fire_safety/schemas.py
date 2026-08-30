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
ANALYSIS_SCHEMA_PATH = PROJECT_ROOT / "schemas" / "analysis_result.schema.json"

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


class LegalRelation(StrEnum):
    """How directly a clause relates to visible evidence."""

    DIRECT = "direct"
    CONDITIONAL = "conditional"


class PenaltyAssociation(VisualModel):
    """A verified legal-liability clause linked to a resolved legal clause."""

    clause_id: NonEmptyStr
    source_name: NonEmptyStr
    clause_number: NonEmptyStr
    clause_text: NonEmptyStr
    missing_conditions: list[NonEmptyStr]


class LegalAssociation(VisualModel):
    """A clause deterministically resolved from an Issue Code."""

    clause_id: NonEmptyStr
    source_name: NonEmptyStr
    clause_number: NonEmptyStr
    clause_text: NonEmptyStr
    relation: LegalRelation
    missing_conditions: list[NonEmptyStr]
    penalties: list[PenaltyAssociation] = Field(default_factory=list)


class AnalysisEvidence(VisualModel):
    """Evidence projected to pixel-independent bbox payloads for the result."""

    text: NonEmptyStr
    bboxes: list[NormalizedBBox]


class RuleStatus(StrEnum):
    """Outcome of applying the local rule catalog to a Finding's Issue Codes."""

    MATCHED = "matched"
    NO_VALID_ISSUE_CODE = "no_valid_issue_code"
    NO_BINDING = "no_binding"


class AnalysisFinding(VisualModel):
    """Finding enriched with deterministic legal associations."""

    finding_id: NonEmptyStr
    title: NonEmptyStr
    description: NonEmptyStr
    risk_priority: RiskPriority
    risk_mechanism: NonEmptyStr
    evidence: Annotated[list[AnalysisEvidence], Field(min_length=1)]
    legal_associations: list[LegalAssociation]
    limitations: list[NonEmptyStr]
    recommended_action: NonEmptyStr
    rule_status: RuleStatus
    rule_warnings: list[NonEmptyStr]


class AnalysisStatus(StrEnum):
    """Top-level analysis lifecycle status values."""

    COMPLETED = "completed"
    NO_FINDINGS = "no_findings"
    IMAGE_UNUSABLE = "image_unusable"
    MODEL_FAILED = "model_failed"
    INVALID_MODEL_OUTPUT = "invalid_model_output"


class AnalysisResult(VisualModel):
    """Public result envelope consumed by the future pipeline and UI."""

    status: AnalysisStatus
    message: NonEmptyStr | None = None
    findings: list[AnalysisFinding]

    def to_payload(self) -> dict[str, object]:
        """Serialize to the canonical ``analysis_result.schema.json`` payload.

        ``message`` is omitted rather than emitted as ``null``: the checked-in
        schema declares it optional and typed ``string``, and forbids
        additional properties. Every consumer that serializes a result must go
        through here so the wire format stays schema-valid.
        """

        return self.model_dump(mode="json", exclude_none=True)


def load_visual_investigation_schema(
    path: str | Path = VISUAL_SCHEMA_PATH,
) -> dict[str, object]:
    """Load the checked-in JSON Schema sent to the model provider."""

    return _load_schema(path, "visual investigation")


def load_analysis_result_schema(
    path: str | Path = ANALYSIS_SCHEMA_PATH,
) -> dict[str, object]:
    """Load the checked-in JSON Schema describing the pipeline result."""

    return _load_schema(path, "analysis result")


def _load_schema(path: str | Path, label: str) -> dict[str, object]:
    schema_path = Path(path)
    with schema_path.open(encoding="utf-8") as schema_file:
        schema = json.load(schema_file)
    if not isinstance(schema, dict):
        raise ValueError(f"{label} schema must be a JSON object")
    return schema


__all__ = [
    "ANALYSIS_SCHEMA_PATH",
    "Evidence",
    "AnalysisFinding",
    "AnalysisEvidence",
    "AnalysisResult",
    "AnalysisStatus",
    "Finding",
    "LegalAssociation",
    "LegalRelation",
    "NormalizedBBox",
    "PenaltyAssociation",
    "RiskPriority",
    "RuleStatus",
    "VISUAL_SCHEMA_PATH",
    "VisualInvestigation",
    "VisualRegion",
    "load_analysis_result_schema",
    "load_visual_investigation_schema",
]
