from __future__ import annotations

import json

import pytest

from fire_safety import PROJECT_ROOT
from fire_safety.rules import (
    RuleDataError,
    load_rule_catalog,
    recommended_action,
    resolve_issue_codes,
    resolve_legal_associations,
    resolve_rule_status,
)
from fire_safety.schemas import AnalysisFinding, AnalysisResult, RuleStatus


def test_checked_in_rule_catalog_loads_and_resolves() -> None:
    catalog = load_rule_catalog()

    assert len(catalog.issue_codes) > 0
    assert resolve_issue_codes(
        ["PASSAGE_OBSTRUCTED", "UNKNOWN", "PASSAGE_OBSTRUCTED"], catalog
    ) == ("PASSAGE_OBSTRUCTED",)

    associations = resolve_legal_associations(["PASSAGE_OBSTRUCTED"], catalog)
    assert len(associations) == 3
    assert [item.relation.value for item in associations] == [
        "conditional",
        "conditional",
        "conditional",
    ]
    assert len({item.clause_id for item in associations}) == 3


@pytest.mark.parametrize(
    "issue_code",
    ["FIRE_FACILITY_OBSCURED", "EVACUATION_SIGN_OBSCURED"],
)
def test_gb55037_6_5_1_is_reachable_from_obscured_issue_codes(issue_code: str) -> None:
    catalog = load_rule_catalog()

    associations = resolve_legal_associations([issue_code], catalog)

    association = next(item for item in associations if item.clause_id == "GB55037-6.5.1")
    assert association.relation.value == "conditional"
    assert association.missing_conditions


def test_direct_bindings_sort_before_conditional_bindings(tmp_path) -> None:
    issue_path, binding_path, clause_path = _write_catalog_files(tmp_path)
    catalog = load_rule_catalog(issue_path, binding_path, clause_path)

    associations = resolve_legal_associations(["CODE"], catalog)

    assert [item.clause_id for item in associations] == ["T-DIRECT", "T-CONDITIONAL"]


def test_legacy_include_extensions_keyword_remains_callable(tmp_path) -> None:
    paths = _write_catalog_files(tmp_path)

    catalog = load_rule_catalog(*paths, include_extensions=True)

    assert catalog.catalog_id == "test"


def test_missing_binding_keeps_finding_actionable() -> None:
    catalog = load_rule_catalog()

    assert resolve_legal_associations(["UNKNOWN"], catalog) == ()
    assert recommended_action(["UNKNOWN"], catalog).startswith("建议现场核验")
    status, warnings = resolve_rule_status(["UNKNOWN"], catalog)
    assert status is RuleStatus.NO_VALID_ISSUE_CODE
    assert warnings

    unbound_catalog = catalog.model_copy(update={"bindings": ()})
    status, warnings = resolve_rule_status(["PASSAGE_OBSTRUCTED"], unbound_catalog)
    assert status is RuleStatus.NO_BINDING
    assert warnings


def test_unknown_clause_reference_is_rejected(tmp_path) -> None:
    issue_path, binding_path, clause_path = _write_catalog_files(tmp_path)
    payload = json.loads(binding_path.read_text())
    payload["bindings"][0]["clause_id"] = "MISSING"
    binding_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(RuleDataError):
        load_rule_catalog(issue_path, binding_path, clause_path)


def test_clause_source_code_must_match_clause_id_prefix(tmp_path) -> None:
    issue_path, binding_path, clause_path = _write_catalog_files(tmp_path)
    payload = json.loads(clause_path.read_text(encoding="utf-8"))
    payload["clauses"][0]["source_code"] = "OTHER"
    clause_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(RuleDataError):
        load_rule_catalog(issue_path, binding_path, clause_path)


def test_checked_in_clauses_cite_their_own_source() -> None:
    catalog = load_rule_catalog()

    names: dict[str, set[str]] = {}
    for clause in catalog.clauses:
        names.setdefault(clause.source_code, set()).add(clause.source_name)

    assert names == {
        "XFF": {"中华人民共和国消防法"},
        "GLGD": {"机关、团体、企业、事业单位消防安全管理规定"},
        "GB55037": {"建筑防火通用规范 GB 55037-2022"},
        "GBT13869": {"用电安全导则 GB/T 13869-2017"},
        "GB55036": {"消防设施通用规范 GB 55036-2022"},
    }


def test_recommended_action_ranks_priority_by_severity_not_alphabet(tmp_path) -> None:
    issue_path, binding_path, clause_path = _write_catalog_files(tmp_path)
    payload = json.loads(issue_path.read_text(encoding="utf-8"))
    payload["issue_codes"].extend(
        {
            "code": code,
            "display_name": code,
            "definition": code,
            "default_priority": priority,
            "default_action": f"{priority} 动作",
        }
        for code, priority in (("MED", "medium"), ("LOW", "low"))
    )
    issue_path.write_text(json.dumps(payload), encoding="utf-8")
    catalog = load_rule_catalog(issue_path, binding_path, clause_path)

    # "low" sorts before "medium" alphabetically; severity must win instead.
    assert recommended_action(["LOW", "MED"], catalog) == "medium 动作"
    assert recommended_action(["LOW", "MED", "CODE"], catalog) == "采取措施"


def test_priority_orders_bindings_within_the_same_relation(tmp_path) -> None:
    paths = _write_catalog_files(
        tmp_path,
        bindings=[_binding("T-C", 3), _binding("T-A", 1), _binding("T-B", 2)],
        clause_ids=("T-A", "T-B", "T-C"),
    )
    catalog = load_rule_catalog(*paths)

    associations = resolve_legal_associations(["CODE"], catalog)

    assert [item.clause_id for item in associations] == ["T-A", "T-B", "T-C"]


def test_duplicate_clauses_are_dropped_before_the_visible_cap(tmp_path) -> None:
    paths = _write_catalog_files(
        tmp_path,
        bindings=[
            _binding("T-A", 1),
            _binding("T-A", 2),  # same clause reached through a second binding
            _binding("T-B", 3),
            _binding("T-C", 4),
        ],
        clause_ids=("T-A", "T-B", "T-C"),
        max_visible=2,
    )
    catalog = load_rule_catalog(*paths)

    associations = resolve_legal_associations(["CODE"], catalog)

    assert [item.clause_id for item in associations] == ["T-A", "T-B"]


def test_unknown_issue_code_reference_is_rejected(tmp_path) -> None:
    issue_path, binding_path, clause_path = _write_catalog_files(tmp_path)
    payload = json.loads(binding_path.read_text(encoding="utf-8"))
    payload["bindings"][0]["issue_code"] = "MISSING"
    binding_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(RuleDataError):
        load_rule_catalog(issue_path, binding_path, clause_path)


@pytest.mark.parametrize("target", ["issue_codes", "bindings", "clauses"])
def test_duplicate_ids_are_rejected(tmp_path, target) -> None:
    paths = dict(zip(("issue_codes", "bindings", "clauses"), _write_catalog_files(tmp_path)))
    payload = json.loads(paths[target].read_text(encoding="utf-8"))
    payload[target].append(dict(payload[target][0]))
    paths[target].write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(RuleDataError):
        load_rule_catalog(paths["issue_codes"], paths["bindings"], paths["clauses"])


def test_repealed_clause_is_excluded_and_reported(tmp_path) -> None:
    issue_path, binding_path, clause_path = _write_catalog_files(tmp_path)
    payload = json.loads(clause_path.read_text(encoding="utf-8"))
    for clause in payload["clauses"]:
        clause["effective"] = clause["clause_id"] != "T-DIRECT"
    clause_path.write_text(json.dumps(payload), encoding="utf-8")
    catalog = load_rule_catalog(issue_path, binding_path, clause_path)

    status, warnings = resolve_rule_status(["CODE"], catalog)
    associations = resolve_legal_associations(["CODE"], catalog)

    assert [item.clause_id for item in associations] == ["T-CONDITIONAL"]
    assert status is RuleStatus.MATCHED
    assert "条款已失效，未展示：《测试法规》第一条（T-DIRECT）" in warnings


def test_bound_code_with_every_clause_repealed_reports_no_binding(tmp_path) -> None:
    issue_path, binding_path, clause_path = _write_catalog_files(tmp_path)
    payload = json.loads(clause_path.read_text(encoding="utf-8"))
    for clause in payload["clauses"]:
        clause["effective"] = False
    clause_path.write_text(json.dumps(payload), encoding="utf-8")
    catalog = load_rule_catalog(issue_path, binding_path, clause_path)

    status, warnings = resolve_rule_status(["CODE"], catalog)

    # "CODE" is bound, so a code-only check would wrongly report matched;
    # every clause it reaches is repealed, so nothing can actually be shown.
    assert resolve_legal_associations(["CODE"], catalog) == ()
    assert status is RuleStatus.NO_BINDING
    assert any("失效" in warning for warning in warnings)


def test_duplicate_bindings_to_one_clause_keep_first_and_warn(tmp_path) -> None:
    # One code bound to the same clause twice: the second binding is dropped
    # from the visible list, leaving a warning instead of a silent dedup.
    issue_path, binding_path, clause_path = _write_catalog_files(
        tmp_path,
        bindings=[
            _binding("T-DIRECT", 1),
            _binding("T-DIRECT", 2, "direct"),
        ],
        clause_ids=("T-DIRECT",),
    )
    catalog = load_rule_catalog(issue_path, binding_path, clause_path)

    status, warnings = resolve_rule_status(["CODE"], catalog)
    associations = resolve_legal_associations(["CODE"], catalog)

    assert [item.clause_id for item in associations] == ["T-DIRECT"]
    assert status is RuleStatus.MATCHED
    assert warnings.count("多条规则绑定对应相同法规条款：《测试法规》第一条（T-DIRECT）") == 1


def test_duplicate_clause_warnings_are_aggregated_for_multiple_issue_codes() -> None:
    catalog = load_rule_catalog()

    _status, warnings = resolve_rule_status(["PASSAGE_OBSTRUCTED", "EXIT_AREA_BLOCKED"], catalog)

    duplicate_warnings = [
        warning for warning in warnings if warning.startswith("多条规则绑定对应相同法规条款")
    ]
    assert len(duplicate_warnings) == 1
    assert "XFF-28" in duplicate_warnings[0]
    assert "GB55037-7.1.5" in duplicate_warnings[0]


def test_status_never_claims_matched_without_clauses() -> None:
    catalog = load_rule_catalog()

    for definition in catalog.issue_codes:
        status, _ = resolve_rule_status([definition.code], catalog)
        associations = resolve_legal_associations([definition.code], catalog)
        assert (status is RuleStatus.MATCHED) == bool(associations), definition.code


def test_example_result_stays_in_sync_with_the_rule_catalog() -> None:
    example_path = PROJECT_ROOT / "examples" / "analysis_result.example.json"
    result = AnalysisResult.model_validate(json.loads(example_path.read_text(encoding="utf-8")))
    catalog = load_rule_catalog()
    finding = result.findings[0]

    expected = resolve_legal_associations(["PASSAGE_OBSTRUCTED"], catalog)
    assert list(finding.legal_associations) == list(expected)
    assert finding.recommended_action == recommended_action(["PASSAGE_OBSTRUCTED"], catalog)


def test_analysis_result_exposes_rule_status() -> None:
    catalog = load_rule_catalog()
    codes = ["PASSAGE_OBSTRUCTED", "BOGUS"]
    status, warnings = resolve_rule_status(codes, catalog)

    result = AnalysisResult(
        status="completed",
        findings=[
            AnalysisFinding(
                finding_id="F1",
                title="人员通行空间被占用",
                description="纸箱占据通行空间。",
                risk_priority="high",
                risk_mechanism="可能影响疏散。",
                evidence=[{"text": "纸箱连续占据通行区域。", "bboxes": []}],
                legal_associations=list(resolve_legal_associations(codes, catalog)),
                limitations=[],
                recommended_action=recommended_action(codes, catalog),
                rule_status=status,
                rule_warnings=list(warnings),
            )
        ],
    )

    finding = result.findings[0]
    assert finding.rule_status is RuleStatus.MATCHED
    assert finding.rule_warnings == ["未知问题代码：BOGUS"]
    assert len(finding.legal_associations) == 3


def _binding(clause_id, priority, relation="conditional"):
    return {
        "binding_id": f"B-{clause_id}-{priority}",
        "issue_code": "CODE",
        "clause_id": clause_id,
        "priority": priority,
        "relation": relation,
        "missing_conditions": ["需确认"] if relation == "conditional" else [],
    }


def _write_catalog_files(tmp_path, bindings=None, clause_ids=None, max_visible=3):
    issue_path = tmp_path / "issue_codes.json"
    binding_path = tmp_path / "rule_bindings.json"
    clause_path = tmp_path / "clauses.json"
    if bindings is None:
        bindings = [
            _binding("T-CONDITIONAL", 1),
            _binding("T-DIRECT", 99, "direct"),
        ]
    if clause_ids is None:
        clause_ids = ("T-DIRECT", "T-CONDITIONAL")
    issue_path.write_text(
        json.dumps(
            {
                "issue_codes": [
                    {
                        "code": "CODE",
                        "display_name": "测试",
                        "definition": "测试",
                        "default_priority": "high",
                        "default_action": "采取措施",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    binding_path.write_text(
        json.dumps(
            {
                "max_visible_clauses_per_finding": max_visible,
                "bindings": bindings,
            }
        ),
        encoding="utf-8",
    )
    clause_template = {
        "source_name": "测试法规",
        "source_code": "T",
        "clause_number": "第一条",
        "clause_text": "条款内容",
        "effective": True,
        "verified_at": "2026-01-01",
        "verification_source": "test",
        "engineering_note": "说明",
    }
    clauses = [{"clause_id": clause_id, **clause_template} for clause_id in clause_ids]
    clause_path.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "catalog_id": "test",
                "clauses": clauses,
            }
        ),
        encoding="utf-8",
    )
    return issue_path, binding_path, clause_path
