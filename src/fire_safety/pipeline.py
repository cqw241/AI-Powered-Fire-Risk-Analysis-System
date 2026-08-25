"""End-to-end orchestration for fire-risk analysis."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Protocol, TypeAlias

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
    _resolve_clauses,
    _rule_status,
    get_rule_catalog,
    recommended_action,
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


class QwenAnalyzer(Protocol):
    """Callable contract for the model stage.

    Implementations always receive ``settings`` by keyword; ``None`` means
    "use the process-wide settings". The return value is validated by the
    pipeline, so a test double may return a raw payload instead of a
    :class:`VisualInvestigation`.
    """

    async def __call__(
        self, image: PreparedImage, *, settings: Settings | None = None
    ) -> object: ...


class VisualCleanupError(ValueError):
    """Raised when a visual result cannot be cleaned into a usable form.

    Reserved for defects that make the whole response untrustworthy: a
    duplicated ``finding_id`` destroys the identity key the result is keyed
    on. Region- and bbox-level defects are cleaned in place instead, so a
    single bad region never discards a Finding.
    """


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

    # Resolved before the model call: a broken local rule package is a local
    # misconfiguration, and discovering it must not cost a paid Qwen request.
    # 设计文档 §13 buckets local catalog failures under `model_failed`.
    try:
        catalog = rule_catalog or get_rule_catalog()
    except RuleDataError as exc:
        return _error_result(AnalysisStatus.MODEL_FAILED, str(exc))

    try:
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
        return _error_result(AnalysisStatus(exc.status.value), str(exc))

    findings = _apply_rules(cleaned_findings, catalog)
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
    finding_ids = [finding.finding_id for finding in visual.findings]
    if len(finding_ids) != len(set(finding_ids)):
        raise VisualCleanupError("视觉结果包含重复的 finding_id")

    valid_regions = _valid_regions(visual)
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


def _valid_regions(visual: VisualInvestigation) -> dict[str, list[int]]:
    """Map each trustworthy ``region_id`` to its normalized bbox.

    A duplicated ``region_id`` makes every copy ambiguous — nothing says which
    bbox an Evidence reference meant — so all copies are dropped rather than
    silently resolving to the first one. Geometrically invalid bboxes are
    dropped the same way. In both cases the referencing Finding survives with
    the offending reference removed, per 设计文档 §7 and §13.
    """

    occurrences = Counter(region.region_id for region in visual.regions)
    valid: dict[str, list[int]] = {}
    for region in visual.regions:
        if occurrences[region.region_id] > 1:
            continue
        try:
            valid[region.region_id] = list(validate_bbox_1000(region.bbox_1000))
        except (InvalidBoundingBox, TypeError):
            continue
    return valid


def _apply_rules(
    cleaned_findings: tuple[tuple[Finding, list[AnalysisEvidence]], ...],
    catalog: RuleCatalog,
) -> list[AnalysisFinding]:
    """Apply rules while retaining the complete visual Finding payload."""

    results: list[AnalysisFinding] = []
    for finding, evidence in cleaned_findings:
        _valid, associations, warnings = _resolve_clauses(
            finding.suggested_issue_codes, catalog
        )
        rule_status = _rule_status(_valid, associations)
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


__all__ = ["VisualCleanupError", "analyze", "QwenAnalyzer", "ImageSource"]
