"""Manifest-driven loading for declarative risk packs."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal, Sequence

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StringConstraints,
    ValidationError,
)

from fire_safety import PROJECT_ROOT
from fire_safety.schemas import LegalRelation, RiskPriority

LEGAL_DATA_DIR = PROJECT_ROOT / "data" / "legal"
RISK_PACKS_DIR = LEGAL_DATA_DIR / "risk_packs"
MANIFEST_FILENAME = "manifest.json"
ISSUE_CODES_FILENAME = "issue_codes.json"
RULE_BINDINGS_FILENAME = "rule_bindings.json"
CLAUSES_FILENAME = "clauses.json"

NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class RuleDataError(ValueError):
    """Raised when declarative rule data cannot be loaded or validated."""


class RiskPackManifest(BaseModel):
    """Metadata controlling discovery and enablement of one risk pack."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"]
    pack_id: NonEmptyStr
    version: NonEmptyStr
    catalog_id: NonEmptyStr
    enabled: StrictBool


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
        catalog_id: str = "three-file-catalog",
        max_visible_clauses_per_finding: int = 3,
    ) -> RuleCatalog:
        """Build a catalog from the supported three-file payload shape."""

        try:
            issue_items = _array_payload(issue_codes, "issue_codes")
            binding_items = _array_payload(bindings, "bindings")
            clause_items = _array_payload(clauses, "clauses")
        except (TypeError, ValueError, KeyError) as exc:
            raise RuleDataError(f"法规规则包结构或引用无效：{exc}") from exc
        return _build_rule_catalog(
            issue_codes=issue_items,
            bindings=binding_items,
            clauses=clause_items,
            schema_version=schema_version,
            catalog_id=catalog_id,
            max_visible_clauses_per_finding=max_visible_clauses_per_finding,
            error_context="法规规则包结构或引用无效",
        )


class _LoadedRiskPack(BaseModel):
    model_config = ConfigDict(extra="forbid")

    manifest: RiskPackManifest
    issue_codes: tuple[IssueCodeDefinition, ...]
    bindings: tuple[RuleBinding, ...]
    clauses: tuple[Clause, ...]


def load_risk_pack_catalog(root: str | Path = RISK_PACKS_DIR) -> RuleCatalog:
    """Discover enabled risk packs below ``root`` and merge them into one catalog."""

    root_path = Path(root)
    try:
        manifest_paths = sorted(
            root_path.glob(f"*/{MANIFEST_FILENAME}"), key=lambda path: path.parent.name
        )
    except OSError as exc:
        raise RuleDataError(f"无法扫描 Risk Pack 目录：{root_path}") from exc
    if not manifest_paths:
        raise RuleDataError(f"Risk Pack 目录中未发现 manifest.json：{root_path}")

    manifests = [_load_manifest(path) for path in manifest_paths]
    _validate_unique_with_sources(
        [
            (manifest.pack_id, path.parent.name)
            for path, manifest in zip(manifest_paths, manifests, strict=True)
        ],
        "pack_id",
    )
    enabled = [
        _load_enabled_pack(path.parent, manifest)
        for path, manifest in zip(manifest_paths, manifests, strict=True)
        if manifest.enabled
    ]
    if not enabled:
        raise RuleDataError(f"Risk Pack 目录中没有已启用的规则包：{root_path}")

    _validate_pack_item_uniqueness(enabled, "issue_codes", "code")
    _validate_pack_item_uniqueness(enabled, "bindings", "binding_id")
    _validate_pack_item_uniqueness(enabled, "clauses", "clause_id")

    catalog_id = "+".join(pack.manifest.catalog_id for pack in enabled)
    pack_ids = ", ".join(pack.manifest.pack_id for pack in enabled)
    return _build_rule_catalog(
        issue_codes=tuple(item for pack in enabled for item in pack.issue_codes),
        bindings=tuple(item for pack in enabled for item in pack.bindings),
        clauses=tuple(item for pack in enabled for item in pack.clauses),
        schema_version="1.0",
        catalog_id=catalog_id,
        max_visible_clauses_per_finding=3,
        error_context=f"Risk Pack 合并后结构或引用无效（{pack_ids}）",
    )


def load_rule_catalog(
    issue_codes_path: str | Path | None = None,
    bindings_path: str | Path | None = None,
    clauses_path: str | Path | None = None,
) -> RuleCatalog:
    """Load enabled Risk Packs, or an explicitly supplied three-file catalog."""

    selected_paths = (issue_codes_path, bindings_path, clauses_path)
    if all(path is None for path in selected_paths):
        return load_risk_pack_catalog()
    if any(path is None for path in selected_paths):
        raise RuleDataError("显式加载规则目录时必须同时提供三条 JSON 文件路径")

    issue_payload = _read_json(issue_codes_path, "three-file catalog")
    binding_payload = _read_json(bindings_path, "three-file catalog")
    clause_payload = _read_json(clauses_path, "three-file catalog")
    return RuleCatalog.from_raw(
        issue_payload,
        binding_payload,
        clause_payload,
        schema_version=_string_field(clause_payload, "schema_version", "1.0"),
        catalog_id=_string_field(clause_payload, "catalog_id", "three-file-catalog"),
        max_visible_clauses_per_finding=_int_field(
            binding_payload, "max_visible_clauses_per_finding", 3
        ),
    )


def load_issue_code_definitions(path: str | Path) -> tuple[IssueCodeDefinition, ...]:
    """Validate an explicitly supplied Issue Code file for prompt compatibility."""

    try:
        definitions = tuple(
            IssueCodeDefinition.model_validate(item)
            for item in _array_payload(_read_json(path, "Issue Code catalog"), "issue_codes")
        )
        _validate_unique(definitions, "code")
    except (TypeError, ValueError, ValidationError) as exc:
        raise RuleDataError(f"Issue Code 目录结构无效：{exc}") from exc
    return definitions


@lru_cache
def get_rule_catalog() -> RuleCatalog:
    """Return the process-wide merged catalog of enabled Risk Packs."""

    return load_risk_pack_catalog()


def _load_manifest(path: Path) -> RiskPackManifest:
    payload = _read_json(path, path.parent.name)
    try:
        return RiskPackManifest.model_validate(payload)
    except ValidationError as exc:
        raise RuleDataError(f"Risk Pack manifest 无效（{path.parent.name}）：{exc}") from exc


def _load_enabled_pack(directory: Path, manifest: RiskPackManifest) -> _LoadedRiskPack:
    context = f"Risk Pack {manifest.pack_id}"
    try:
        issue_codes = tuple(
            IssueCodeDefinition.model_validate(item)
            for item in _array_payload(
                _read_json(directory / ISSUE_CODES_FILENAME, context), "issue_codes"
            )
        )
        bindings = tuple(
            RuleBinding.model_validate(item)
            for item in _array_payload(
                _read_json(directory / RULE_BINDINGS_FILENAME, context), "bindings"
            )
        )
        clauses = tuple(
            Clause.model_validate(item)
            for item in _array_payload(_read_json(directory / CLAUSES_FILENAME, context), "clauses")
        )
        pack = _LoadedRiskPack(
            manifest=manifest,
            issue_codes=issue_codes,
            bindings=bindings,
            clauses=clauses,
        )
        _validate_unique(pack.issue_codes, "code")
        _validate_unique(pack.bindings, "binding_id")
        _validate_unique(pack.clauses, "clause_id")
    except (TypeError, ValueError, ValidationError) as exc:
        raise RuleDataError(f"{context} 数据结构无效：{exc}") from exc
    return pack


def _read_json(path: str | Path, context: str) -> object:
    try:
        with Path(path).open(encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise RuleDataError(f"无法读取 {context} JSON 文件：{path}") from exc


def _array_payload(payload: object, field: str) -> list[object]:
    if not isinstance(payload, dict) or not isinstance(payload.get(field), list):
        raise ValueError(f"{field} must be an array")
    return payload[field]


def _build_rule_catalog(
    *,
    issue_codes: Sequence[object],
    bindings: Sequence[object],
    clauses: Sequence[object],
    schema_version: str,
    catalog_id: str,
    max_visible_clauses_per_finding: int,
    error_context: str,
) -> RuleCatalog:
    """Build and cross-validate the catalog used by every loading entry point."""

    try:
        catalog = RuleCatalog.model_validate(
            {
                "schema_version": schema_version,
                "catalog_id": catalog_id,
                "issue_codes": issue_codes,
                "bindings": bindings,
                "clauses": clauses,
                "max_visible_clauses_per_finding": max_visible_clauses_per_finding,
            }
        )
        _validate_catalog(catalog)
    except (TypeError, ValueError, KeyError, ValidationError) as exc:
        raise RuleDataError(f"{error_context}：{exc}") from exc
    return catalog


def _validate_catalog(catalog: RuleCatalog) -> None:
    _validate_unique(catalog.issue_codes, "code")
    _validate_unique(catalog.bindings, "binding_id")
    _validate_unique(catalog.clauses, "clause_id")
    for clause in catalog.clauses:
        if clause.clause_id.split("-", 1)[0] != clause.source_code:
            raise ValueError(f"clause id prefix does not match source_code: {clause.clause_id}")
    issue_set = {item.code for item in catalog.issue_codes}
    clause_set = {item.clause_id for item in catalog.clauses}
    for binding in catalog.bindings:
        if binding.issue_code not in issue_set:
            raise ValueError(f"binding references unknown issue code: {binding.issue_code}")
        if binding.clause_id not in clause_set:
            raise ValueError(f"binding references unknown clause: {binding.clause_id}")


def _validate_pack_item_uniqueness(
    packs: Sequence[_LoadedRiskPack], collection: str, field: str
) -> None:
    values = [
        (str(getattr(item, field)), pack.manifest.pack_id)
        for pack in packs
        for item in getattr(pack, collection)
    ]
    _validate_unique_with_sources(values, field)


def _validate_unique_with_sources(values: Sequence[tuple[str, str]], field: str) -> None:
    seen: dict[str, str] = {}
    for value, source in values:
        if value in seen:
            raise RuleDataError(
                f"Risk Pack 标识冲突：duplicate {field} {value} in {seen[value]} and {source}"
            )
        seen[value] = source


def _validate_unique(items: Sequence[BaseModel], field: str) -> None:
    values = [getattr(item, field) for item in items]
    if len(values) != len(set(values)):
        raise ValueError(f"duplicate {field}")


def _string_field(payload: object, field: str, default: str) -> str:
    return payload.get(field, default) if isinstance(payload, dict) else default


def _int_field(payload: object, field: str, default: int) -> int:
    return payload.get(field, default) if isinstance(payload, dict) else default


__all__ = [
    "CLAUSES_FILENAME",
    "ISSUE_CODES_FILENAME",
    "LEGAL_DATA_DIR",
    "MANIFEST_FILENAME",
    "RISK_PACKS_DIR",
    "RULE_BINDINGS_FILENAME",
    "Clause",
    "IssueCodeDefinition",
    "RiskPackManifest",
    "RuleBinding",
    "RuleCatalog",
    "RuleDataError",
    "get_rule_catalog",
    "load_issue_code_definitions",
    "load_risk_pack_catalog",
    "load_rule_catalog",
]
