from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest

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
