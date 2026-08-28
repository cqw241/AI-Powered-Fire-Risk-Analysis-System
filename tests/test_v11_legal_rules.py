from __future__ import annotations

import json

from fire_safety.qwen import build_visual_prompt
from fire_safety.risk_packs import RISK_PACKS_DIR, load_risk_pack_catalog
from fire_safety.rules import (
    get_rule_catalog,
    load_rule_catalog,
    resolve_legal_associations,
)


def test_no_argument_loader_maps_to_the_runtime_catalog() -> None:
    assert load_rule_catalog() == get_rule_catalog()


def test_runtime_catalog_is_loaded_from_the_enabled_risk_packs() -> None:
    catalog = get_rule_catalog()

    counts = (len(catalog.issue_codes), len(catalog.bindings), len(catalog.clauses))
    assert catalog.catalog_id == "cn-mainland-electrical-v1+cn-mainland-fire-v1.2"
    assert counts == (
        33,
        70,
        39,
    )
    clause_ids = {item.clause_id for item in catalog.clauses}
    issue_codes = {item.code for item in catalog.issue_codes}

    assert {
        "GB55036-2.0.9",
        "GB55036-2.0.10",
        "GB55036-4.0.5-2",
        "GB55036-10.0.4",
    } <= clause_ids
    assert {
        "SPRINKLER_OBSTRUCTED",
        "FIRE_FACILITY_MARKING_OBSCURED_OR_DEFECTIVE",
        "ELECTRICAL_WIRING_VISIBLE_DAMAGE",
    } <= issue_codes
    assert "GB55024-10.4.1" in clause_ids
    assert "SUSPECTED_COMBUSTIBLE_NEAR_ELECTRICAL_PRODUCT" not in issue_codes
    assert {"GB55024-8.7.6-3", "GB55024-8.7.8-2"} <= {
        item.clause_id
        for item in catalog.bindings
        if item.issue_code == "WIRING_ENTRY_DAMAGE_OR_PROTECTION_MISSING"
    }
    assert catalog == load_risk_pack_catalog(RISK_PACKS_DIR)


def test_electrical_rules_are_owned_by_the_electrical_pack() -> None:
    electrical_dir = RISK_PACKS_DIR / "cn-mainland-electrical-safety"
    fire_dir = RISK_PACKS_DIR / "cn-mainland-fire-safety"
    electrical_codes = {
        item["code"]
        for item in json.loads((electrical_dir / "issue_codes.json").read_text())["issue_codes"]
    }
    electrical_clauses = {
        item["clause_id"]
        for item in json.loads((electrical_dir / "clauses.json").read_text())["clauses"]
    }
    fire_codes = {
        item["code"]
        for item in json.loads((fire_dir / "issue_codes.json").read_text())["issue_codes"]
    }
    assert "ELECTRICAL_WIRING_VISIBLE_HAZARD" in electrical_codes
    assert "SUSPECTED_COMBUSTIBLE_NEAR_ELECTRICAL_PRODUCT" not in electrical_codes
    assert {"XFF-27-2", "GB55037-10.2.3", "GBT13869-5.1.1-3"} <= electrical_clauses
    assert "ELECTRICAL_WIRING_VISIBLE_HAZARD" not in fire_codes
    assert "SUSPECTED_COMBUSTIBLE_NEAR_ELECTRICAL_PRODUCT" not in fire_codes


def test_power_strip_daisy_chain_is_intentionally_unbound() -> None:
    catalog = get_rule_catalog()

    assert "POWER_STRIP_DAISY_CHAIN" in {item.code for item in catalog.issue_codes}
    assert not any(
        item.issue_code == "POWER_STRIP_DAISY_CHAIN" for item in catalog.bindings
    )


def test_verified_gb55036_clause_text_is_exact_checked_in_text() -> None:
    catalog = get_rule_catalog()
    clauses = {item.clause_id: item for item in catalog.clauses}

    assert clauses["GB55036-2.0.9"].clause_text == (
        "消防设施投入使用后，应定期进行巡查、检查和维护，并应保证其处于正常运行或工作状态，"
        "不应擅自关停、拆改或移动。超过有效期的灭火介质、消防设施或经检验不符合继续使用要求的"
        "管道、组件和压力容器不应使用。"
    )
    assert clauses["GB55036-2.0.10"].clause_text == (
        "消防设施上或附近应设置区别于环境的明显标识，说明文字应准确、清楚且易于识别，"
        "颜色、符号或标志应规范。手动操作按钮等装置处应采取防止误操作或被损坏的防护措施。"
    )
    assert clauses["GB55036-4.0.5-2"].clause_text == (
        "喷头周围不应有遮挡或影响洒水效果的障碍物；"
    )
    assert clauses["GB55036-10.0.4"].clause_text == (
        "灭火器应设置在位置明显和便于取用的地点，且不应影响人员安全疏散。"
        "当确需设置在有视线障碍的设置点时，应设置指示灭火器位置的醒目标志。"
    )


def test_sprinkler_obstruction_maps_directly_to_item_4_0_5_2() -> None:
    associations = resolve_legal_associations(["SPRINKLER_OBSTRUCTED"], get_rule_catalog())

    assert len(associations) == 1
    assert associations[0].clause_id == "GB55036-4.0.5-2"
    assert associations[0].relation.value == "direct"
    assert associations[0].missing_conditions == []


def test_fire_facility_marking_maps_directly_to_2_0_10() -> None:
    associations = resolve_legal_associations(
        ["FIRE_FACILITY_MARKING_OBSCURED_OR_DEFECTIVE"], get_rule_catalog()
    )

    assert len(associations) == 1
    assert associations[0].clause_id == "GB55036-2.0.10"
    assert associations[0].relation.value == "direct"


def test_existing_extinguisher_issue_prefers_gb55036_direct_rule() -> None:
    associations = resolve_legal_associations(["EXTINGUISHER_OBSTRUCTED"], get_rule_catalog())

    assert associations[0].clause_id == "GB55036-10.0.4"
    assert associations[0].relation.value == "direct"


def test_existing_disabled_facility_issue_reaches_2_0_9_conditionally() -> None:
    associations = resolve_legal_associations(
        ["FIRE_FACILITY_DAMAGED_OR_DISABLED"], get_rule_catalog()
    )

    association = next(item for item in associations if item.clause_id == "GB55036-2.0.9")
    assert association.relation.value == "conditional"
    assert association.missing_conditions


def test_visual_prompt_includes_v11_issue_codes() -> None:
    prompt = build_visual_prompt()

    assert "`SPRINKLER_OBSTRUCTED`" in prompt
    assert "`FIRE_FACILITY_MARKING_OBSCURED_OR_DEFECTIVE`" in prompt
