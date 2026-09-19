from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest

from fire_safety.settings import Settings


@pytest.fixture(autouse=True)
def isolated_settings_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the developer's shell out of ``Settings`` defaults.

    ``Settings`` reads environment variables, and passing a field explicitly
    only pins that one field — every other field still falls back to the
    ambient environment. A shell that exports ``QWEN_*`` (or an IDE that loads
    ``.env``) therefore silently changes which defaults a test exercises: a
    default such as ``qwen_reasoning_effort=None`` stops being the default.
    """

    for field in Settings.model_fields:
        monkeypatch.delenv(field.upper(), raising=False)

VALID_VISUAL_OUTPUT: dict[str, Any] = {
    "scene_summary": "室内通行区域，可见纸箱和消防设施",
    "regions": [
        {
            "region_id": "R1",
            "bbox_1000": [120, 410, 810, 950],
            "label": "堆放纸箱",
        }
    ],
    "findings": [
        {
            "finding_id": "F1",
            "title": "人员通行空间被占用",
            "description": "多个纸箱占据画面中的通行空间。",
            "risk_mechanism": "紧急情况下可能影响人员快速通行。",
            "risk_priority": "high",
            "evidence": [
                {
                    "text": "纸箱连续占据通行区域。",
                    "region_ids": ["R1"],
                }
            ],
            "suggested_issue_codes": ["PASSAGE_OBSTRUCTED"],
            "limitations": ["无法仅凭图片确认该区域的法定消防用途。"],
        }
    ],
}


@pytest.fixture
def valid_visual_output() -> dict[str, Any]:
    return deepcopy(VALID_VISUAL_OUTPUT)
