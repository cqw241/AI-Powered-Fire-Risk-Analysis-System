from __future__ import annotations

import pytest

from fire_safety.rules import load_rule_catalog, resolve_legal_associations
from fire_safety.schemas import (
    AnalysisEvidence,
    AnalysisFinding,
    AnalysisResult,
    AnalysisStatus,
    LegalAssociation,
    LegalRelation,
    PenaltyAssociation,
    RiskPriority,
    RuleStatus,
)
from fire_safety.ui import render_result_html


@pytest.mark.parametrize(
    ("issue_code", "legal_clause_id", "penalty_clause_id"),
    [
        ("PASSAGE_OBSTRUCTED", "XFF-28", "XFF-60-1-3"),
        ("EXIT_AREA_BLOCKED", "XFF-28", "XFF-60-1-3"),
        ("EXIT_DOOR_LOCKED_VISIBLE", "XFF-28", "XFF-60-1-3"),
        ("HYDRANT_OBSTRUCTED", "XFF-28", "XFF-60-1-4"),
        ("FIRE_LANE_OBSTRUCTION_CONCERN", "XFF-28", "XFF-60-1-5"),
        ("ESCAPE_RESCUE_OBSTACLE_PRESENT", "XFF-28", "XFF-60-1-6"),
        ("MIXED_USE_SLEEPING_CONCERN", "XFF-19-1", "XFF-61-1"),
        ("OPEN_FLAME_VISIBLE", "XFF-21-1", "XFF-63-2"),
        ("SMOKING_VISIBLE", "XFF-21-1", "XFF-63-2"),
        ("HAZARDOUS_MATERIAL_STORAGE_CONCERN", "XFF-23-1", "XFF-62-1"),
        ("ELECTRICAL_WIRING_VISIBLE_HAZARD", "XFF-27-2", "XFF-66"),
        ("EV_BATTERY_INDOOR_CHARGING_CONCERN", "GCMJZ-37-1", "GCMJZ-47-7"),
    ],
)
def test_checked_in_penalty_overlays_resolve(
    issue_code: str, legal_clause_id: str, penalty_clause_id: str
) -> None:
    catalog = load_rule_catalog()

    associations = resolve_legal_associations([issue_code], catalog)
    legal = next(item for item in associations if item.clause_id == legal_clause_id)

    assert penalty_clause_id in {item.clause_id for item in legal.penalties}


def test_mixed_use_other_goods_maps_to_second_paragraph_penalty() -> None:
    catalog = load_rule_catalog()

    associations = resolve_legal_associations(["MIXED_USE_SLEEPING_CONCERN"], catalog)
    legal = next(item for item in associations if item.clause_id == "XFF-19-2")
    penalty = next(item for item in legal.penalties if item.clause_id == "XFF-61-2")

    assert any("不符合适用的消防技术标准" in item for item in penalty.missing_conditions)


def test_penalties_merge_when_multiple_issue_codes_share_one_legal_clause() -> None:
    catalog = load_rule_catalog()

    associations = resolve_legal_associations(
        ["PASSAGE_OBSTRUCTED", "HYDRANT_OBSTRUCTED"], catalog
    )
    xff28 = next(item for item in associations if item.clause_id == "XFF-28")

    assert [item.clause_id for item in xff28.penalties] == [
        "XFF-60-1-3",
        "XFF-60-1-4",
    ]


def test_shared_penalty_does_not_and_conditions_from_alternative_issue_codes() -> None:
    catalog = load_rule_catalog()

    associations = resolve_legal_associations(
        ["SMOKING_VISIBLE", "OPEN_FLAME_VISIBLE"], catalog
    )
    legal = next(item for item in associations if item.clause_id == "XFF-21-1")
    penalty = next(item for item in legal.penalties if item.clause_id == "XFF-63-2")

    assert penalty.missing_conditions == []


def test_penalty_specific_conditions_do_not_duplicate_parent_conditions() -> None:
    catalog = load_rule_catalog()

    associations = resolve_legal_associations(
        ["EV_BATTERY_INDOOR_CHARGING_CONCERN"], catalog
    )
    legal = next(item for item in associations if item.clause_id == "GCMJZ-37-1")
    penalty = next(item for item in legal.penalties if item.clause_id == "GCMJZ-47-7")

    assert legal.missing_conditions
    assert penalty.missing_conditions == ["需确认经消防救援机构责令改正后拒不改正。"]


def test_unmapped_issue_does_not_gain_penalty_by_clause_only() -> None:
    catalog = load_rule_catalog()

    associations = resolve_legal_associations(["FIRE_FACILITY_DAMAGED_OR_DISABLED"], catalog)
    xff28 = next(item for item in associations if item.clause_id == "XFF-28")

    assert xff28.penalties == []


def test_ui_renders_penalty_original_text_and_non_adjudication_notice() -> None:
    penalty = PenaltyAssociation(
        clause_id="XFF-60-1-3",
        source_name="中华人民共和国消防法",
        clause_number="第六十条第一款第三项及第二、三款",
        clause_text="处罚原文测试文本",
        missing_conditions=["需确认该区域属于法定疏散通道。"],
    )
    legal = LegalAssociation(
        clause_id="XFF-28",
        source_name="中华人民共和国消防法",
        clause_number="第二十八条",
        clause_text="实体条款测试文本",
        relation=LegalRelation.CONDITIONAL,
        missing_conditions=["需确认该区域属于法定疏散通道。"],
        penalties=[penalty],
    )
    second_legal = legal.model_copy(
        update={"clause_id": "XFF-19-1", "penalties": [penalty]}
    )
    finding = AnalysisFinding(
        finding_id="F1",
        title="通道受阻",
        description="测试",
        risk_priority=RiskPriority.HIGH,
        risk_mechanism="测试",
        evidence=[AnalysisEvidence(text="测试证据", bboxes=[])],
        legal_associations=[legal, second_legal],
        limitations=[],
        recommended_action="清理障碍物。",
        rule_status=RuleStatus.MATCHED,
        rule_warnings=[],
    )
    result = AnalysisResult(status=AnalysisStatus.COMPLETED, findings=[finding])

    rendered = render_result_html(result)

    assert "相关处罚规定" in rendered
    assert "处罚原文测试文本" in rendered
    assert rendered.count("不构成违法认定或处罚决定") == 1
