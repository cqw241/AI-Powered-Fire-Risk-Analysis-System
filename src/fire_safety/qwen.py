"""Qwen OpenAI-compatible client for structured image analysis."""

from __future__ import annotations

import json
from enum import StrEnum
from pathlib import Path

from fire_safety import PROJECT_ROOT

PROMPT_PATH = PROJECT_ROOT / "prompts" / "visual_investigator.md"
ISSUE_CODES_PATH = PROJECT_ROOT / "data" / "legal" / "issue_codes.json"
ISSUE_CATALOG_PLACEHOLDER = "{{ISSUE_CATALOG}}"


class QwenStatus(StrEnum):
    """Pipeline-facing status values produced by the model stage."""

    MODEL_FAILED = "model_failed"
    INVALID_MODEL_OUTPUT = "invalid_model_output"


class QwenError(RuntimeError):
    """Base error for failures in the model stage."""

    def __init__(self, message: str, *, reason: str):
        super().__init__(message)
        self.reason = reason


class QwenConfigurationError(QwenError):
    """Raised before a request when required local configuration is missing."""

    status = QwenStatus.MODEL_FAILED


def build_visual_prompt(
    prompt_path: str | Path = PROMPT_PATH,
    issue_codes_path: str | Path = ISSUE_CODES_PATH,
) -> str:
    """Load the visual prompt and inject the controlled Issue Code catalog."""

    try:
        template = Path(prompt_path).read_text(encoding="utf-8")
        catalog_data = json.loads(Path(issue_codes_path).read_text(encoding="utf-8"))
        issue_codes = catalog_data["issue_codes"]
        if not isinstance(issue_codes, list):
            raise TypeError("issue_codes must be an array")
        catalog_lines = []
        for item in issue_codes:
            code = item["code"]
            definition = item["definition"]
            if not isinstance(code, str) or not code.strip():
                raise TypeError("issue code must be a non-empty string")
            if not isinstance(definition, str) or not definition.strip():
                raise TypeError("issue definition must be a non-empty string")
            catalog_lines.append(f"- `{code}`：{definition}")
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise QwenConfigurationError(
            "视觉分析 Prompt 或 Issue Code 目录无法加载",
            reason="invalid_prompt_resources",
        ) from exc

    if template.count(ISSUE_CATALOG_PLACEHOLDER) != 1:
        raise QwenConfigurationError(
            "视觉分析 Prompt 必须包含一个 Issue Code 目录占位符",
            reason="invalid_prompt_template",
        )
    return template.replace(ISSUE_CATALOG_PLACEHOLDER, "\n".join(catalog_lines))


__all__ = [
    "ISSUE_CODES_PATH",
    "PROMPT_PATH",
    "QwenConfigurationError",
    "QwenError",
    "QwenStatus",
    "build_visual_prompt",
]
