"""End-to-end orchestration for fire-risk analysis."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TypeAlias

from pydantic import ValidationError

from fire_safety.image import (
    ImageProcessingError,
    InvalidBoundingBox,
    PreparedImage,
    prepare_image,
    validate_bbox_1000,
)
from fire_safety.qwen import (
    InvalidModelOutputError,
    QwenError,
    analyze_image,
)
from fire_safety.rules import (
    RuleCatalog,
    RuleDataError,
    load_rule_catalog,
    recommended_action,
    resolve_legal_associations,
    resolve_rule_status,
)
from fire_safety.schemas import (
    AnalysisEvidence,
    AnalysisFinding,
    AnalysisResult,
    AnalysisStatus,
    Finding,
    VisualInvestigation,
)
from fire_safety.settings import Settings

ImageSource: TypeAlias = PreparedImage | bytes | bytearray | str | Path
QwenAnalyzer: TypeAlias = Callable[..., Awaitable[object]]


class VisualCleanupError(ValueError):
    """Raised when a visual result violates a uniqueness invariant."""


async def analyze(
    image: ImageSource,
    *,
    qwen_analyzer: QwenAnalyzer = analyze_image,
    rule_catalog: RuleCatalog | None = None,
    settings: Settings | None = None,
) -> AnalysisResult:
    """Run one image through preparation, Qwen, cleanup, and rule resolution."""

    try:
        prepared = image if isinstance(image, PreparedImage) else prepare_image(image, settings)
    except ImageProcessingError as exc:
        return _error_result(AnalysisStatus.IMAGE_UNUSABLE, str(exc))

    try:
        if settings is None:
            raw_visual = await qwen_analyzer(prepared)
        else:
            raw_visual = await qwen_analyzer(prepared, settings=settings)
        visual = _coerce_visual_investigation(raw_visual)
        cleaned_findings = _clean_visual_findings(visual)
    except InvalidModelOutputError as exc:
        return _error_result(AnalysisStatus.INVALID_MODEL_OUTPUT, str(exc))
    except VisualCleanupError as exc:
        return _error_result(AnalysisStatus.INVALID_MODEL_OUTPUT, str(exc))
    except ValidationError as exc:
        return _error_result(AnalysisStatus.INVALID_MODEL_OUTPUT, _validation_message(exc))
    except QwenError as exc:
        status = AnalysisStatus(str(exc.status.value))
        return _error_result(status, str(exc))

    try:
        catalog = rule_catalog or load_rule_catalog()
        findings = _apply_rules(cleaned_findings, catalog)
    except RuleDataError as exc:
        return _error_result(AnalysisStatus.MODEL_FAILED, str(exc))

    if not findings:
        return AnalysisResult(
            status=AnalysisStatus.NO_FINDINGS,
            message="当前图片可见范围内未发现明确的消防风险。",
            findings=[],
        )
    return AnalysisResult(status=AnalysisStatus.COMPLETED, findings=findings)


def _coerce_visual_investigation(raw_visual: object) -> VisualInvestigation:
    if isinstance(raw_visual, VisualInvestigation):
        return raw_visual
    return VisualInvestigation.model_validate(raw_visual)


def _clean_visual_findings(
    visual: VisualInvestigation,
) -> tuple[tuple[Finding, list[AnalysisEvidence]], ...]:
    region_ids = [region.region_id for region in visual.regions]
    if len(region_ids) != len(set(region_ids)):
        raise VisualCleanupError("视觉结果包含重复的 region_id")

    finding_ids = [finding.finding_id for finding in visual.findings]
    if len(finding_ids) != len(set(finding_ids)):
        raise VisualCleanupError("视觉结果包含重复的 finding_id")

    valid_regions: dict[str, list[int]] = {}
    for region in visual.regions:
        try:
            bbox = list(validate_bbox_1000(region.bbox_1000))
        except (InvalidBoundingBox, TypeError):
            continue
        valid_regions[region.region_id] = bbox

    cleaned: list[tuple[Finding, list[AnalysisEvidence]]] = []
    for finding in visual.findings:
        evidence_items: list[AnalysisEvidence] = []
        for evidence in finding.evidence:
            bboxes = [
                valid_regions[region_id]
                for region_id in evidence.region_ids
                if region_id in valid_regions
            ]
            evidence_items.append(AnalysisEvidence(text=evidence.text, bboxes=bboxes))
        cleaned.append((finding, evidence_items))
    return tuple(cleaned)


def _apply_rules(
    cleaned_findings: tuple[tuple[Finding, list[AnalysisEvidence]], ...],
    catalog: RuleCatalog,
) -> list[AnalysisFinding]:
    """Apply rules while retaining the complete visual Finding payload."""

    results: list[AnalysisFinding] = []
    for finding, evidence in cleaned_findings:
        associations = resolve_legal_associations(finding.suggested_issue_codes, catalog)
        rule_status, warnings = resolve_rule_status(finding.suggested_issue_codes, catalog)
        results.append(
            AnalysisFinding(
                finding_id=finding.finding_id,
                title=finding.title,
                description=finding.description,
                risk_priority=finding.risk_priority,
                risk_mechanism=finding.risk_mechanism,
                evidence=evidence,
                legal_associations=list(associations),
                limitations=list(finding.limitations),
                recommended_action=recommended_action(
                    finding.suggested_issue_codes, catalog
                ),
                rule_status=rule_status,
                rule_warnings=list(warnings),
            )
        )
    return results


def _error_result(status: AnalysisStatus, message: str) -> AnalysisResult:
    return AnalysisResult(
        status=status,
        message=message.strip() or status.value,
        findings=[],
    )


def _validation_message(error: ValidationError) -> str:
    return f"Qwen 返回的结构化结果无效：{error.errors()[0]['msg']}"


__all__ = ["analyze"]
