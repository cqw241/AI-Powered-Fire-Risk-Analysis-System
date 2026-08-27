"""Deterministic Issue Code to legal-clause resolution."""

from __future__ import annotations

from collections import Counter
from typing import Sequence

from fire_safety.risk_packs import (
    Clause,
    IssueCodeDefinition,
    RuleBinding,
    RuleCatalog,
    RuleDataError,
    get_rule_catalog,
    load_rule_catalog,
)
from fire_safety.schemas import (
    LegalAssociation,
    LegalRelation,
    RiskPriority,
    RuleStatus,
)

_PRIORITY_RANK = {RiskPriority.HIGH: 0, RiskPriority.MEDIUM: 1, RiskPriority.LOW: 2}


def resolve_issue_codes(
    suggested_issue_codes: Sequence[str], catalog: RuleCatalog
) -> tuple[str, ...]:
    allowed = {item.code for item in catalog.issue_codes}
    return tuple(dict.fromkeys(code for code in suggested_issue_codes if code in allowed))


def resolve_rule_status(
    suggested_issue_codes: Sequence[str], catalog: RuleCatalog
) -> tuple[RuleStatus, tuple[str, ...]]:
    """Return an auditable status and warnings for a Finding's Issue Codes."""

    _valid, associations, warnings = _resolve_clauses(suggested_issue_codes, catalog)
    return _rule_status(_valid, associations), tuple(warnings)


def _rule_status(
    valid_codes: Sequence[str], associations: Sequence[LegalAssociation]
) -> RuleStatus:
    """Derive the status from values already resolved in one pass."""

    if not valid_codes:
        return RuleStatus.NO_VALID_ISSUE_CODE
    if not associations:
        return RuleStatus.NO_BINDING
    return RuleStatus.MATCHED


def resolve_legal_associations(
    issue_codes: Sequence[str], catalog: RuleCatalog
) -> tuple[LegalAssociation, ...]:
    _valid, associations, _warnings = _resolve_clauses(issue_codes, catalog)
    return associations


def _resolve_clauses(
    issue_codes: Sequence[str], catalog: RuleCatalog
) -> tuple[tuple[str, ...], tuple[LegalAssociation, ...], list[str]]:
    """Compute legal associations and their audit trail in one pass."""

    valid_codes = resolve_issue_codes(issue_codes, catalog)
    clauses = {item.clause_id: item for item in catalog.clauses}
    display_names = {item.code: item.display_name for item in catalog.issue_codes}
    bindings = [binding for binding in catalog.bindings if binding.issue_code in valid_codes]

    warnings: list[str] = []
    unknown = tuple(dict.fromkeys(code for code in issue_codes if code not in valid_codes))
    warnings.extend(f"未知问题代码：{code}" for code in unknown)
    if not valid_codes:
        if not unknown:
            warnings.append("该风险未给出问题代码，未关联法规。")
        return valid_codes, tuple(), warnings

    bound_codes = {binding.issue_code for binding in catalog.bindings}
    warnings.extend(
        f"问题代码无法规绑定：{code}（{display_names[code]}）"
        for code in valid_codes
        if code not in bound_codes
    )
    retired = dict.fromkeys(
        binding.clause_id for binding in bindings if not clauses[binding.clause_id].effective
    )
    warnings.extend(
        f"条款已失效，未展示：{_clause_label(clauses[clause_id])}" for clause_id in retired
    )

    bindings.sort(
        key=lambda item: (0 if item.relation is LegalRelation.DIRECT else 1, item.priority)
    )
    results: list[LegalAssociation] = []
    seen: set[str] = set()
    for binding in bindings:
        if binding.clause_id in seen:
            continue
        if not clauses[binding.clause_id].effective:
            continue
        seen.add(binding.clause_id)
        clause = clauses[binding.clause_id]
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

    duplicated = tuple(
        dict.fromkeys(
            clause_id
            for clause_id, count in Counter(binding.clause_id for binding in bindings).items()
            if count > 1
        )
    )
    if duplicated:
        labels = "、".join(_clause_label(clauses[clause_id]) for clause_id in duplicated)
        warnings.append("多条规则绑定对应相同法规条款：" + labels)
    return valid_codes, tuple(results), warnings


def _clause_label(clause: Clause) -> str:
    """Human-readable clause citation: 《source_name》clause_number（clause_id）."""

    return f"《{clause.source_name}》{clause.clause_number}（{clause.clause_id}）"


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


__all__ = [
    "Clause",
    "IssueCodeDefinition",
    "RuleBinding",
    "RuleCatalog",
    "RuleDataError",
    "get_rule_catalog",
    "load_rule_catalog",
    "recommended_action",
    "resolve_issue_codes",
    "resolve_legal_associations",
    "resolve_rule_status",
]
