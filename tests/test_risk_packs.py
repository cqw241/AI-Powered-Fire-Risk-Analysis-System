from __future__ import annotations

import json

import pytest

from fire_safety.risk_packs import RuleDataError, load_risk_pack_catalog


def test_discovers_and_merges_domain_packs_in_directory_order(tmp_path) -> None:
    _write_pack(tmp_path, "03-gas", "gas", "GAS")
    _write_pack(tmp_path, "01-fire", "fire", "FIRE")
    _write_pack(tmp_path, "02-electrical", "electrical", "ELECTRICAL")

    catalog = load_risk_pack_catalog(tmp_path)

    assert catalog.catalog_id == "fire-catalog+electrical-catalog+gas-catalog"
    assert [item.code for item in catalog.issue_codes] == ["FIRE", "ELECTRICAL", "GAS"]
    assert [item.binding_id for item in catalog.bindings] == [
        "RB-FIRE",
        "RB-ELECTRICAL",
        "RB-GAS",
    ]


def test_disabled_pack_only_requires_a_valid_manifest(tmp_path) -> None:
    _write_pack(tmp_path, "enabled", "enabled", "ACTIVE")
    disabled = tmp_path / "disabled"
    disabled.mkdir()
    _write_json(disabled / "manifest.json", _manifest("disabled", enabled=False))

    catalog = load_risk_pack_catalog(tmp_path)

    assert [item.code for item in catalog.issue_codes] == ["ACTIVE"]


def test_disabled_pack_with_invalid_manifest_is_rejected(tmp_path) -> None:
    _write_pack(tmp_path, "enabled", "enabled", "ACTIVE")
    disabled = tmp_path / "disabled"
    disabled.mkdir()
    manifest = _manifest("disabled", enabled=False)
    manifest["unexpected"] = True
    _write_json(disabled / "manifest.json", manifest)

    with pytest.raises(RuleDataError, match="manifest 无效"):
        load_risk_pack_catalog(tmp_path)


def test_no_enabled_pack_is_rejected(tmp_path) -> None:
    disabled = tmp_path / "disabled"
    disabled.mkdir()
    _write_json(disabled / "manifest.json", _manifest("disabled", enabled=False))

    with pytest.raises(RuleDataError, match="没有已启用"):
        load_risk_pack_catalog(tmp_path)


def test_enabled_pack_with_missing_data_file_is_rejected(tmp_path) -> None:
    directory = _write_pack(tmp_path, "broken", "broken", "BROKEN")
    (directory / "clauses.json").unlink()

    with pytest.raises(RuleDataError, match="broken"):
        load_risk_pack_catalog(tmp_path)


def test_enabled_pack_with_malformed_json_is_rejected(tmp_path) -> None:
    directory = _write_pack(tmp_path, "broken", "broken", "BROKEN")
    (directory / "issue_codes.json").write_text("not json", encoding="utf-8")

    with pytest.raises(RuleDataError, match="broken"):
        load_risk_pack_catalog(tmp_path)


def test_duplicate_ids_across_packs_report_both_pack_ids(tmp_path) -> None:
    _write_pack(tmp_path, "first", "first", "SAME")
    _write_pack(tmp_path, "second", "second", "SAME")

    with pytest.raises(RuleDataError, match=r"duplicate code SAME in first and second"):
        load_risk_pack_catalog(tmp_path)


def test_binding_can_reference_data_from_another_enabled_pack(tmp_path) -> None:
    base = _write_pack(tmp_path, "base", "base", "SHARED")
    _write_json(base / "rule_bindings.json", {"bindings": []})
    extension = _write_pack(tmp_path, "extension", "extension", "UNUSED")
    _write_json(extension / "issue_codes.json", {"issue_codes": []})
    _write_json(extension / "clauses.json", {"clauses": []})
    _write_json(
        extension / "rule_bindings.json",
        {"bindings": [_binding("SHARED", "T-SHARED", "RB-CROSS-PACK")]},
    )

    catalog = load_risk_pack_catalog(tmp_path)

    assert [item.binding_id for item in catalog.bindings] == ["RB-CROSS-PACK"]


def test_unknown_cross_pack_reference_is_rejected_after_merge(tmp_path) -> None:
    directory = _write_pack(tmp_path, "pack", "pack", "KNOWN")
    _write_json(
        directory / "rule_bindings.json",
        {"bindings": [_binding("UNKNOWN", "T-KNOWN", "RB-UNKNOWN")]},
    )

    with pytest.raises(RuleDataError, match="unknown issue code: UNKNOWN"):
        load_risk_pack_catalog(tmp_path)


def _write_pack(tmp_path, directory_name, pack_id, code, *, enabled=True):
    directory = tmp_path / directory_name
    directory.mkdir()
    clause_id = f"T-{code}"
    _write_json(directory / "manifest.json", _manifest(pack_id, enabled=enabled))
    _write_json(
        directory / "issue_codes.json",
        {
            "issue_codes": [
                {
                    "code": code,
                    "display_name": code,
                    "definition": f"{code} definition",
                    "default_priority": "medium",
                    "default_action": f"Handle {code}",
                }
            ]
        },
    )
    _write_json(
        directory / "rule_bindings.json",
        {"bindings": [_binding(code, clause_id, f"RB-{code}")]},
    )
    _write_json(
        directory / "clauses.json",
        {
            "clauses": [
                {
                    "clause_id": clause_id,
                    "source_name": "Test source",
                    "source_code": "T",
                    "clause_number": "1",
                    "clause_text": f"Clause for {code}",
                    "effective": True,
                    "verified_at": "2026-08-27",
                    "verification_source": "test",
                    "engineering_note": "test",
                }
            ]
        },
    )
    return directory


def _manifest(pack_id, *, enabled):
    return {
        "schema_version": "1.0",
        "pack_id": pack_id,
        "version": "1.0",
        "catalog_id": f"{pack_id}-catalog",
        "enabled": enabled,
    }


def _binding(issue_code, clause_id, binding_id):
    return {
        "binding_id": binding_id,
        "issue_code": issue_code,
        "clause_id": clause_id,
        "priority": 1,
        "relation": "direct",
        "missing_conditions": [],
    }


def _write_json(path, payload):
    path.write_text(json.dumps(payload), encoding="utf-8")
