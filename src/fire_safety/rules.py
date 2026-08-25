"""Deterministic Issue Code to legal-clause resolution."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Sequence

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StringConstraints

from fire_safety import PROJECT_ROOT
from fire_safety.schemas import (
    LegalAssociation,
    LegalRelation,
    RiskPriority,
    RuleStatus,
)

LEGAL_DATA_DIR = PROJECT_ROOT / "data" / "legal"
ISSUE_CODES_PATH = LEGAL_DATA_DIR / "issue_codes.json"
RULE_BINDINGS_PATH = LEGAL_DATA_DIR / "rule_bindings.json"
CLAUSES_PATH = LEGAL_DATA_DIR / "clauses.json"

NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]

_PRIORITY_RANK = {RiskPriority.HIGH: 0, RiskPriority.MEDIUM: 1, RiskPriority.LOW: 2}


class RuleDataError(ValueError):
    """Raised when local rule data cannot be loaded or cross-referenced."""


class IssueCodeDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: NonEmptyStr
    display_name: NonEmptyStr
    definition: NonEmptyStr
    default_priority: RiskPriority
    default_action: NonEmptyStr


class RuleBinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    binding_id: NonEmptyStr
    issue_code: NonEmptyStr
    clause_id: NonEmptyStr
    priority: Annotated[StrictInt, Field(gt=0)]
    relation: LegalRelation
    missing_conditions: list[NonEmptyStr]


class Clause(BaseModel):
    model_config = ConfigDict(extra="forbid")

    clause_id: NonEmptyStr
    source_name: NonEmptyStr
    source_code: NonEmptyStr
    clause_number: NonEmptyStr
    clause_text: NonEmptyStr
    effective: bool
    verified_at: NonEmptyStr
    verification_source: NonEmptyStr
    engineering_note: NonEmptyStr


class RuleCatalog(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: NonEmptyStr
    catalog_id: NonEmptyStr
    issue_codes: tuple[IssueCodeDefinition, ...]
    bindings: tuple[RuleBinding, ...]
    clauses: tuple[Clause, ...]
    max_visible_clauses_per_finding: Annotated[StrictInt, Field(gt=0)] = 3

    @classmethod
    def from_raw(
        cls,
        issue_codes: object,
        bindings: object,
        clauses: object,
        *,
        schema_version: str = "1.0",
        catalog_id: str = "cn-mainland-v1-clauses",
        max_visible_clauses_per_finding: int = 3,
    ) -> "RuleCatalog":
        issue_items = _array_payload(issue_codes, "issue_codes")
        binding_items = _array_payload(bindings, "bindings")
        clause_items = _array_payload(clauses, "clauses")
        catalog = cls.model_validate(
            {
                "schema_version": schema_version,
                "catalog_id": catalog_id,
                "issue_codes": issue_items,
                "bindings": binding_items,
                "clauses": clause_items,
                "max_visible_clauses_per_finding": max_visible_clauses_per_finding,
            }
        )
        _validate_unique(catalog.issue_codes, "code")
        _validate_unique(catalog.bindings, "binding_id")
        _validate_unique(catalog.clauses, "clause_id")
        for clause in catalog.clauses:
            if clause.clause_id.split("-", 1)[0] != clause.source_code:
                raise ValueError(
                    f"clause id prefix does not match source_code: {clause.clause_id}"
                )
        issue_set = {item.code for item in catalog.issue_codes}
        clause_set = {item.clause_id for item in catalog.clauses}
        for binding in catalog.bindings:
            if binding.issue_code not in issue_set:
                raise ValueError(f"binding references unknown issue code: {binding.issue_code}")
            if binding.clause_id not in clause_set:
                raise ValueError(f"binding references unknown clause: {binding.clause_id}")
        return catalog


def load_rule_catalog(
    issue_codes_path: str | Path = ISSUE_CODES_PATH,
    bindings_path: str | Path = RULE_BINDINGS_PATH,
    clauses_path: str | Path = CLAUSES_PATH,
) -> RuleCatalog:
    """Load and validate the checked-in legal rule package."""

    issue_payload = _read_json(issue_codes_path)
    binding_payload = _read_json(bindings_path)
    clause_payload = _read_json(clauses_path)
    try:
        return RuleCatalog.from_raw(
            issue_payload,
            binding_payload,
            clause_payload,
            schema_version=_string_field(clause_payload, "schema_version", "1.0"),
            catalog_id=_string_field(clause_payload, "catalog_id", "cn-mainland-v1-clauses"),
            max_visible_clauses_per_finding=_int_field(
                binding_payload, "max_visible_clauses_per_finding", 3
            ),
        )
    except (TypeError, ValueError, KeyError) as exc:
        raise RuleDataError(f"法规规则包结构或引用无效：{exc}") from exc


def resolve_issue_codes(
    suggested_issue_codes: Sequence[str], catalog: RuleCatalog
) -> tuple[str, ...]:
    allowed = {item.code for item in catalog.issue_codes}
    return tuple(dict.fromkeys(code for code in suggested_issue_codes if code in allowed))


def resolve_rule_status(
    suggested_issue_codes: Sequence[str], catalog: RuleCatalog
) -> tuple[RuleStatus, tuple[str, ...]]:
    """Return an auditable status and warnings for a Finding's Issue Codes.

    The status is derived from the associations actually produced by
    `resolve_legal_associations`, so `matched` always implies a non-empty
    legal list and every dropped clause leaves a warning behind.
    """

    allowed = {item.code for item in catalog.issue_codes}
    unknown = tuple(dict.fromkeys(code for code in suggested_issue_codes if code not in allowed))
    warnings = [f"未知 Issue Code：{code}" for code in unknown]
    valid = resolve_issue_codes(suggested_issue_codes, catalog)
    if not valid:
        if not unknown:
            warnings.append("该风险未给出 Issue Code，未关联法规。")
        return RuleStatus.NO_VALID_ISSUE_CODE, tuple(warnings)

    bound_codes = {binding.issue_code for binding in catalog.bindings}
    warnings.extend(
        f"Issue Code 无法规绑定：{code}" for code in valid if code not in bound_codes
    )
    clauses = {item.clause_id: item for item in catalog.clauses}
    retired = dict.fromkeys(
        binding.clause_id
        for binding in catalog.bindings
        if binding.issue_code in valid and not clauses[binding.clause_id].effective
    )
    warnings.extend(f"条款已失效，未展示：{clause_id}" for clause_id in retired)

    if not resolve_legal_associations(suggested_issue_codes, catalog):
        return RuleStatus.NO_BINDING, tuple(warnings)
    return RuleStatus.MATCHED, tuple(warnings)


def resolve_legal_associations(
    issue_codes: Sequence[str], catalog: RuleCatalog
) -> tuple[LegalAssociation, ...]:
    valid_codes = resolve_issue_codes(issue_codes, catalog)
    clauses = {item.clause_id: item for item in catalog.clauses}
    bindings = [binding for binding in catalog.bindings if binding.issue_code in valid_codes]
    bindings.sort(
        key=lambda item: (0 if item.relation is LegalRelation.DIRECT else 1, item.priority)
    )
    results: list[LegalAssociation] = []
    seen: set[str] = set()
    for binding in bindings:
        if binding.clause_id in seen:
            continue
        clause = clauses[binding.clause_id]
        if not clause.effective:
            continue
        seen.add(binding.clause_id)
        results.append(
            LegalAssociation(
                clause_id=clause.clause_id,
                source_name=clause.source_name,
                clause_number=clause.clause_number,
                clause_text=clause.clause_text,
                relation=binding.relation,
                missing_conditions=list(binding.missing_conditions),
            )
        )
        if len(results) >= catalog.max_visible_clauses_per_finding:
            break
    return tuple(results)


def recommended_action(issue_codes: Sequence[str], catalog: RuleCatalog) -> str:
    valid_codes = resolve_issue_codes(issue_codes, catalog)
    if not valid_codes:
        return "建议现场核验该现象，并优先消除图片中可见的火灾诱因、通行障碍或消防设施使用障碍。"
    definitions = {item.code: item for item in catalog.issue_codes}
    selected = min(
        (definitions[code] for code in valid_codes),
        key=lambda item: (_PRIORITY_RANK[item.default_priority], valid_codes.index(item.code)),
    )
    return selected.default_action


def _read_json(path: str | Path) -> object:
    try:
        with Path(path).open(encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise RuleDataError(f"无法读取法规规则文件: {path}") from exc


def _array_payload(payload: object, field: str) -> object:
    if not isinstance(payload, dict) or not isinstance(payload.get(field), list):
        raise ValueError(f"{field} must be an array")
    return payload[field]


def _string_field(payload: object, field: str, default: str) -> str:
    return payload.get(field, default) if isinstance(payload, dict) else default


def _int_field(payload: object, field: str, default: int) -> int:
    return payload.get(field, default) if isinstance(payload, dict) else default


def _validate_unique(items: Sequence[BaseModel], field: str) -> None:
    values = [getattr(item, field) for item in items]
    if len(values) != len(set(values)):
        raise ValueError(f"duplicate {field}")


__all__ = [
    "CLAUSES_PATH",
    "ISSUE_CODES_PATH",
    "RULE_BINDINGS_PATH",
    "Clause",
    "IssueCodeDefinition",
    "RuleBinding",
    "RuleCatalog",
    "RuleDataError",
    "load_rule_catalog",
    "recommended_action",
    "resolve_issue_codes",
    "resolve_legal_associations",
    "resolve_rule_status",
]
