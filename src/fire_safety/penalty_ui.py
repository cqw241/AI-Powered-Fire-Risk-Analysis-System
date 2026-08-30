"""Penalty-aware UI adapter layered over the existing Gradio renderer.

The base UI stays responsible for page layout and finding rendering. This module
only augments a resolved legal clause with verified legal-liability clauses,
then patches the base renderer before the application is built.
"""

from __future__ import annotations

from fire_safety import ui as _ui
from fire_safety.schemas import LegalAssociation, PenaltyAssociation

_BASE_RENDER_CLAUSE_HTML = _ui._render_clause_html


def _render_penalty_html(penalty: PenaltyAssociation) -> str:
    conditions = "".join(
        f"<li>{_ui._esc(item)}</li>" for item in penalty.missing_conditions
    )
    conditions_html = (
        '<ul class="missing-cond">'
        '<li><strong>处罚适用还需确认：</strong></li>'
        f"{conditions}"
        "</ul>"
        if conditions
        else ""
    )
    return (
        '<details class="clause">'
        "<summary>"
        f'<span class="clause-cite">《{_ui._esc(penalty.source_name)}》 '
        f'{_ui._esc(penalty.clause_number)}</span>'
        f'<code class="clause-id">{_ui._esc(penalty.clause_id)}</code>'
        "</summary>"
        f'<p class="clause-text">{_ui._esc(penalty.clause_text)}</p>'
        "</details>"
        f"{conditions_html}"
    )


def _render_clause_html(law: LegalAssociation) -> str:
    base = _BASE_RENDER_CLAUSE_HTML(law)
    if not law.penalties:
        return base

    penalties = "".join(_render_penalty_html(item) for item in law.penalties)
    notice = (
        '<p class="banner-note">'
        "仅作相关法律责任条款展示，不构成违法认定或处罚决定；"
        "是否满足处罚条件及具体行政处理，由有权机关结合现场事实依法认定。"
        "</p>"
    )
    return (
        f"{base}"
        '<div class="sec">'
        '<span class="sec-label">相关处罚规定</span>'
        f"{penalties}{notice}"
        "</div>"
    )


def install_penalty_renderer() -> None:
    """Install the penalty-aware clause renderer idempotently."""

    if _ui._render_clause_html is not _render_clause_html:
        _ui._render_clause_html = _render_clause_html


install_penalty_renderer()

build_app = _ui.build_app
launch_app = _ui.launch_app
render_empty_html = _ui.render_empty_html
render_loading_html = _ui.render_loading_html
render_result_html = _ui.render_result_html

__all__ = [
    "build_app",
    "install_penalty_renderer",
    "launch_app",
    "render_empty_html",
    "render_loading_html",
    "render_result_html",
]
