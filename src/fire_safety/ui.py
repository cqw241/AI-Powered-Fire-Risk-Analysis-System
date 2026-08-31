"""Gradio page and result rendering for the fire risk analysis system.

The page is deliberately thin: four Gradio components bound to one action. All
structured result presentation happens in ``render_result_html`` and consumes
the ``AnalysisResult`` payload only — the renderer never re-derives facts,
reorders findings, or re-runs rule logic. Design decisions are recorded in
``docs/ui-design.md``.
"""

from __future__ import annotations

import base64
import html
import os
from pathlib import Path
from typing import Any

import gradio as gr
from PIL import Image

from fire_safety.image import ImageProcessingError, PreparedImage, draw_bboxes, prepare_image
from fire_safety.pipeline import analyze
from fire_safety.schemas import (
    AnalysisFinding,
    AnalysisResult,
    AnalysisStatus,
    LegalAssociation,
    LegalRelation,
    PenaltyAssociation,
)
from fire_safety.settings import Settings, get_settings

# System font stacks only, no webfont imports: reliable in mainland-China and
# intranet environments. Noto variants serve as local fallbacks.
_FONT_STACK = (
    '-apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", '
    '"Hiragino Sans GB", "Microsoft YaHei", "Noto Sans CJK SC", '
    '"Source Han Sans SC", "Noto Sans SC", "Helvetica Neue", Arial, sans-serif'
)
_FONT_MONO = 'ui-monospace, "SF Mono", Menlo, Consolas, "Sarasa Mono SC", monospace'
_TOP_BANNER_PATH = Path(__file__).resolve().parent / "assets" / "top_banner.png"

_THEME = gr.themes.Base(
    primary_hue="orange",
    neutral_hue="slate",
    font=_FONT_STACK,
    font_mono=_FONT_MONO,
)

_PRIORITY_META: dict[str, tuple[str, str]] = {
    "high": ("高风险", "prio-high"),
    "medium": ("中风险", "prio-medium"),
    "low": ("低风险", "prio-low"),
}

# status title + recovery hint for terminal analysis failures.
_STATUS_META: dict[AnalysisStatus, tuple[str, str]] = {
    AnalysisStatus.IMAGE_UNUSABLE: ("图片无法使用", "请重新上传 JPEG / PNG / WEBP 图片。"),
    AnalysisStatus.MODEL_FAILED: ("模型分析失败", "请稍后重试；若持续失败，请检查模型配置。"),
    AnalysisStatus.INVALID_MODEL_OUTPUT: ("模型输出无效", "建议更换更清晰的图片后重试。"),
}


def _render_top_banner_html(path: str | Path = _TOP_BANNER_PATH) -> str:
    """Render the supplied branding banner as a self-contained responsive image."""

    banner_path = Path(path)
    try:
        encoded = base64.b64encode(banner_path.read_bytes()).decode("ascii")
    except OSError:
        return ""
    return (
        '<div class="frs-top-banner">'
        f'<img src="data:image/png;base64,{encoded}" '
        'alt="佛山市消防救援局，对党忠诚、纪律严明，赴汤蹈火、竭诚为民" />'
        "</div>"
    )


_TOP_BANNER_HTML = _render_top_banner_html()

_START_SCAN_JS = """() => {
  const sourceImage = document.getElementById("source_image");
  if (sourceImage?.querySelector("img")) {
    sourceImage.classList.add("frs-scanning");
  }
}"""

_STOP_SCAN_JS = """() => {
  document.getElementById("source_image")?.classList.remove("frs-scanning");
}"""

_CSS = (
    f".frs {{ font-family: {_FONT_STACK}; }}"
    + """
.frs {
  --frs-bg: #ffffff;
  --frs-page-bg: #f8fafc;
  --frs-border: #e2e8f0;
  --frs-text: #334155;
  --frs-text-2: #64748b;
  --frs-text-3: #475569;
  --frs-high-fg: #b91c1c;
  --frs-high-bg: #fee2e2;
  --frs-high-bar: #dc2626;
  --frs-med-fg: #b45309;
  --frs-med-bg: #fef3c7;
  --frs-med-bar: #f59e0b;
  --frs-low-fg: #475569;
  --frs-low-bg: #f1f5f9;
  --frs-low-bar: #94a3b8;
  --frs-rel-direct-fg: #1d4ed8;
  --frs-rel-direct-bg: #eff6ff;
  --frs-rel-direct-bd: #bfdbfe;
  --frs-rel-cond-fg: #92400e;
  --frs-rel-cond-bg: #fffbeb;
  --frs-rel-cond-bd: #d97706;
  --frs-error-bg: #fef2f2;
  --frs-error-bd: #fca5a5;
  --frs-error-fg: #991b1b;
  --frs-ok-bg: #ecfdf5;
  --frs-ok-bd: #6ee7b7;
  --frs-ok-fg: #047857;
  color: var(--frs-text);
  min-width: 0;
}
#result_area { min-height: 200px; }

/* The scan line is a transient client-side overlay. It never changes the
   uploaded image or the annotated image returned by the analysis pipeline. */
#source_image.frs-scanning .image-container {
  position: relative;
  overflow: hidden;
}
#source_image.frs-scanning .image-container::after {
  content: "";
  position: absolute;
  z-index: 5;
  top: 0;
  right: 5%;
  left: 5%;
  height: 18%;
  pointer-events: none;
  opacity: .68;
  background: linear-gradient(
    to bottom,
    rgba(56, 189, 248, 0) 0%,
    rgba(56, 189, 248, .06) 42%,
    rgba(125, 211, 252, .28) 68%,
    rgba(186, 230, 253, .72) 72%,
    rgba(56, 189, 248, .12) 78%,
    rgba(56, 189, 248, 0) 100%
  );
  filter: drop-shadow(0 0 7px rgba(56, 189, 248, .3));
  transform: translateY(-120%);
  animation: frs-ai-scan 3.8s linear infinite;
}
@keyframes frs-ai-scan {
  from { transform: translateY(-120%); }
  to { transform: translateY(560%); }
}
@media (prefers-reduced-motion: reduce) {
  #source_image.frs-scanning .image-container::after { animation: none; }
}

/* top_banner 的 elem 落在 Gradio `.block` 上。实测 (Gradio 6.22):
   `.main` 有 padding 16px 32px 且可能居中, 故用 100vw + calc(50% - 50vw)
   抵消左右缩进, margin-top: -16px 抵消顶部 padding, 实现真正全宽贴边;
   `.html-container` 默认 padding "10px 12px", 是横幅四周 12px 白边的来源。
   `.gradio-container` overflow: hidden, 横幅恰在 0..viewport 内, 无溢出问题。 */
#top_banner {
  width: 100vw;
  max-width: none;
  margin: -16px calc(50% - 50vw) 22px;
  padding: 0;
  border: 0;
  border-radius: 0;
  overflow: hidden;
  background: #fff;
}
#top_banner .html-container { padding: 0; }
#top_banner .frs-top-banner {
  width: 100%;
  height: clamp(112px, 16.67vw, 240px);
  overflow: hidden;
  border-radius: 0;
  background: #fff;
}
#top_banner .frs-top-banner img {
  display: block;
  width: 100%;
  height: 100%;
  object-fit: cover;
  object-position: center 47%;
}

.frs .banner {
  padding: 12px 14px;
  border: 1px solid var(--frs-border);
  border-radius: 8px;
  margin-bottom: 14px;
}
.frs .banner p { margin: 0; }
.frs .banner p + p { margin-top: 6px; }
.frs .banner-title { font-size: 15px; font-weight: 700; overflow-wrap: anywhere; }
.frs .banner-msg { font-size: 14.5px; color: var(--frs-text-3); overflow-wrap: anywhere; }
.frs .banner-hint { font-size: 13.5px; color: var(--frs-text-2); }
.frs .banner-note { font-size: 13.5px; color: var(--frs-text-2); }
.frs .banner-info { border-left: 4px solid #f97316; }
.frs .banner-ok { background: var(--frs-ok-bg); border-color: var(--frs-ok-bd); }
.frs .banner-error { background: var(--frs-error-bg); border-color: var(--frs-error-bd); }

.frs .finding {
  background: var(--frs-bg);
  border: 1px solid var(--frs-border);
  border-left: 4px solid var(--frs-border);
  border-radius: 10px;
  padding: 17px 16px;
  margin-bottom: 14px;
}
.frs .finding:last-child { margin-bottom: 0; }
.frs .finding.prio-high { border-left-color: var(--frs-high-bar); }
.frs .finding.prio-medium { border-left-color: var(--frs-med-bar); }
.frs .finding.prio-low { border-left-color: var(--frs-low-bar); }

.frs .finding-head {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  margin-bottom: 10px;
}
.frs .finding-id {
  background: #334155;
  color: #fff;
  border-radius: 6px;
  padding: 1px 8px;
  font-family: ui-monospace, "SF Mono", Menlo, Consolas, monospace;
  font-size: 12px;
  font-weight: 600;
  white-space: nowrap;
}
.frs .finding-title {
  font-size: 17px;
  font-weight: 700;
  color: var(--frs-text);
  margin: 0;
  flex: 1 1 220px;
  line-height: 1.5;
  overflow-wrap: anywhere;
}
.frs .prio {
  border-radius: 6px;
  padding: 3px 10px;
  font-size: 12.5px;
  font-weight: 600;
  white-space: nowrap;
}
.frs .prio-high { background: var(--frs-high-bg); color: var(--frs-high-fg); }
.frs .prio-medium { background: var(--frs-med-bg); color: var(--frs-med-fg); }
.frs .prio-low { background: var(--frs-low-bg); color: var(--frs-low-fg); }

.frs .sec { margin: 0 0 10px; }
.frs .sec:last-child { margin-bottom: 0; }
.frs .sec-label {
  display: block;
  font-size: 12.5px;
  letter-spacing: 0.02em;
  font-weight: 600;
  color: var(--frs-text-2);
  margin: 0 0 5px;
}
.frs p {
  margin: 0 0 6px;
  font-size: 15px;
  line-height: 1.7;
  color: var(--frs-text);
  overflow-wrap: anywhere;
}
.frs p:last-child { margin-bottom: 0; }
.frs .mech { color: var(--frs-text-3); }
.frs ul { margin: 0; padding-left: 20px; }
.frs li { margin: 0 0 4px; line-height: 1.7; color: var(--frs-text); }
.frs li:last-child { margin-bottom: 0; }
.frs .tag {
  display: inline-block;
  background: var(--frs-page-bg);
  border: 1px solid var(--frs-border);
  color: var(--frs-text-3);
  border-radius: 6px;
  padding: 0 7px;
  font-size: 12px;
  margin-left: 6px;
  white-space: nowrap;
}

.frs .clause {
  border: 1px solid var(--frs-border);
  border-radius: 8px;
  background: var(--frs-bg);
  margin: 0 0 8px;
}
.frs .clause > summary {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  padding: 9px 12px;
  cursor: pointer;
  list-style: none;
}
.frs .clause > summary::-webkit-details-marker { display: none; }
.frs .clause > summary::before {
  content: "+";
  color: var(--frs-text-2);
  font-size: 13px;
  font-weight: 600;
}
.frs .clause[open] > summary::before { content: "−"; }
.frs .clause-cite { font-size: 14.5px; font-weight: 600; color: var(--frs-text); }
.frs .rel {
  border-radius: 6px;
  padding: 1px 8px;
  font-size: 12px;
  font-weight: 600;
  white-space: nowrap;
}
.frs .rel-direct {
  color: var(--frs-rel-direct-fg);
  background: var(--frs-rel-direct-bg);
  border: 1px solid var(--frs-rel-direct-bd);
}
.frs .rel-conditional {
  color: var(--frs-rel-cond-fg);
  background: var(--frs-rel-cond-bg);
  border: 1px dashed var(--frs-rel-cond-bd);
}
.frs .clause-id {
  margin-left: auto;
  font-family: ui-monospace, "SF Mono", Menlo, Consolas, monospace;
  font-size: 11.5px;
  color: var(--frs-text-2);
  background: transparent;
  border: none;
  padding: 0;
}
.frs .clause-text {
  margin: 0;
  padding: 10px 12px;
  border-top: 1px solid var(--frs-border);
  font-size: 14.5px;
  line-height: 1.8;
  color: var(--frs-text-3);
  overflow-wrap: anywhere;
}
.frs .missing-cond {
  margin: 6px 0 8px;
  padding: 8px 12px;
  list-style: none;
  background: var(--frs-rel-cond-bg);
  border: 1px dashed var(--frs-rel-cond-bd);
  border-radius: 6px;
  font-size: 13.5px;
  color: var(--frs-rel-cond-fg);
}
.frs .missing-cond li { color: inherit; }
.frs .limits li { font-size: 14px; color: var(--frs-text-3); }
.frs .warns { font-size: 13px; }
.frs .warns li { font-size: 13px; color: var(--frs-text-2); }

.frs .hint { padding: 6px 2px; color: var(--frs-text-2); font-size: 15px; line-height: 1.7; }
.frs .hint-title { margin: 0 0 6px; font-weight: 600; color: var(--frs-text); }
"""
)

_EMPTY_HINT_HTML = (
    '<div class="frs"><div class="hint">'
    '<p class="hint-title">上传图片后点击「开始分析」。</p>'
    "<p>将展示：标注图片 · 风险（优先级 / 证据 / 机理）· 相关法规与缺失条件 · 整改建议。</p>"
    "</div></div>"
)

_LOADING_STAGES: tuple[tuple[str, str], ...] = (
    ("图像预处理", "正在检查图像质量与可分析区域"),
    ("现场视觉理解", "正在识别场景中的关键对象与空间关系"),
    ("风险识别", "正在定位潜在消防风险与可见证据"),
    ("场景风险评估", "结合视觉上下文评估风险优先级"),
    ("规则与法规关联", "匹配消防安全规则及相关法规条件"),
    ("结果校验", "核验风险判断、适用条件与输出完整性"),
)


def _esc(value: object) -> str:
    """Escape untrusted model text before it enters the raw HTML result area."""

    return html.escape(str(value), quote=True)


def render_empty_html() -> str:
    """Return the first-load placeholder shown in the result area."""

    return _EMPTY_HINT_HTML


def render_loading_html() -> str:
    """Return a self-contained visual-only six-stage loading animation."""

    stages = "".join(
        (
            f'<li class="frs-loading-stage" style="--frs-stage-index: {index};">'
            '<span class="frs-loading-marker" aria-hidden="true">'
            '<span class="frs-loading-dot">○</span>'
            '<span class="frs-loading-check">✓</span>'
            "</span>"
            '<span class="frs-loading-copy">'
            f"<strong>{_esc(title)}</strong>"
            f"<span>{_esc(description)}</span>"
            "</span>"
            "</li>"
        )
        for index, (title, description) in enumerate(_LOADING_STAGES)
    )
    return f"""
<div class="frs-loading" role="status" aria-live="polite">
  <div class="frs-loading-heading">处理中的视觉分析</div>
  <div class="frs-loading-panel">
    <ol class="frs-loading-list">{stages}</ol>
    <div class="frs-loading-status" aria-hidden="true">
      <span style="--frs-status-index: 0;">图像预处理 · 处理中</span>
      <span style="--frs-status-index: 1;">现场视觉理解 · 处理中</span>
      <span style="--frs-status-index: 2;">风险识别 · 处理中</span>
      <span style="--frs-status-index: 3;">场景风险评估 · 处理中</span>
      <span style="--frs-status-index: 4;">规则与法规关联 · 处理中</span>
      <span style="--frs-status-index: 5;">结果校验 · 处理中</span>
      <span class="frs-loading-status-final">分析完成，正在展示结果</span>
    </div>
  </div>
</div>
<style>
  .frs-loading {{
    --loading-heading: #0f172a;
    --loading-ink: #0f172a;
    --loading-muted: #475569;
    --loading-panel: #ffffff;
    --loading-panel-border: rgba(15, 23, 42, .14);
    --loading-accent: #0f172a;
    color: var(--loading-heading);
    min-height: 420px;
    padding: 10px 0 0;
    font-family: {_FONT_STACK};
  }}
  .frs-loading-heading {{
    margin: 0 0 28px;
    font-size: 20px;
    font-weight: 650;
    letter-spacing: .02em;
    color: var(--loading-heading) !important;
  }}
  .frs-loading-panel {{
    max-width: 560px;
    padding: 24px 24px 20px;
    border: 1px solid var(--loading-panel-border);
    border-radius: 28px;
    background: var(--loading-panel);
    box-shadow: 0 18px 42px rgba(15, 23, 42, .16);
    color: var(--loading-ink) !important;
  }}
  .frs-loading-list {{
    display: grid;
    gap: 20px;
    margin: 0;
    padding: 0;
    list-style: none;
  }}
  .frs-loading-stage {{
    display: grid;
    grid-template-columns: 20px minmax(0, 1fr);
    column-gap: 10px;
    align-items: start;
    opacity: .82;
    animation: frs-loading-stage-focus 5s linear both;
    animation-delay: calc(var(--frs-stage-index) * 5s);
  }}
  .frs-loading-stage:nth-child(1) {{ animation-delay: 0s; }}
  .frs-loading-stage:nth-child(2) {{ animation-delay: 5s; }}
  .frs-loading-stage:nth-child(3) {{ animation-delay: 10s; }}
  .frs-loading-stage:nth-child(4) {{ animation-delay: 15s; }}
  .frs-loading-stage:nth-child(5) {{ animation-delay: 20s; }}
  .frs-loading-stage:nth-child(6) {{ animation-delay: 25s; }}
  .frs-loading-marker {{
    position: relative;
    display: inline-grid;
    place-items: center;
    width: 18px;
    height: 18px;
    color: var(--loading-ink) !important;
    font-size: 17px;
    line-height: 18px;
    font-weight: 500;
  }}
  .frs-loading-dot,
  .frs-loading-check {{
    position: absolute;
    inset: 0;
    display: grid;
    place-items: center;
  }}
  .frs-loading-dot {{
    color: var(--loading-ink) !important;
    animation: frs-loading-breathe 5s ease-in-out both;
    animation-delay: calc(var(--frs-stage-index) * 5s);
  }}
  .frs-loading-check {{
    color: var(--loading-accent) !important;
    font-size: 16px;
    font-weight: 700;
    opacity: 0;
    animation: frs-loading-check .32s ease-out both;
    animation-delay: calc((var(--frs-stage-index) * 5s) + 4.4s);
  }}
  .frs-loading-copy {{
    display: grid;
    gap: 3px;
    min-width: 0;
  }}
  .frs-loading-copy strong {{
    font-size: 16px;
    font-weight: 600;
    line-height: 1.45;
    color: var(--loading-ink) !important;
  }}
  .frs-loading-copy span {{
    color: var(--loading-muted) !important;
    font-size: 14px;
    line-height: 1.55;
  }}
  .frs-loading-status {{
    position: relative;
    min-height: 22px;
    margin: 24px 0 0 30px;
    color: var(--loading-muted) !important;
    font-size: 13px;
    letter-spacing: .01em;
    overflow: hidden;
  }}
  .frs-loading-status > span {{
    position: absolute;
    inset: 0;
    color: var(--loading-muted) !important;
    opacity: 0;
    transform: translateY(3px);
    animation: frs-loading-status-fade 30s ease both;
    animation-delay: calc(var(--frs-status-index) * 5s);
  }}
  .frs-loading-status-final {{
    animation: frs-loading-status-final 1.2s ease 29.7s both !important;
  }}
  @media (prefers-color-scheme: dark) {{
    .frs-loading {{
      --loading-ink: #f8fafc;
      --loading-muted: #a7b0bd;
      --loading-panel: #202224;
      --loading-panel-border: rgba(255, 255, 255, .06);
      --loading-accent: #f5f7fa;
    }}
  }}
  @keyframes frs-loading-stage-focus {{
    0%, 8% {{ opacity: .82; transform: translateX(0); }}
    10%, 17% {{ opacity: 1; transform: translateX(2px); }}
    18%, 100% {{ opacity: 1; transform: translateX(0); }}
  }}
  @keyframes frs-loading-breathe {{
    0%, 8% {{ transform: scale(1); opacity: .95; text-shadow: 0 0 0 rgba(255,255,255,0); }}
    31% {{ transform: scale(1.12); opacity: 1; text-shadow: 0 0 12px rgba(255,255,255,.55); }}
    70% {{ transform: scale(1); opacity: .95; text-shadow: 0 0 0 rgba(255,255,255,0); }}
    82% {{ transform: scale(1); opacity: .95; }}
    90%, 100% {{ transform: scale(1); opacity: 0; }}
  }}
  @keyframes frs-loading-check {{
    0% {{ transform: scale(.55); opacity: 0; }}
    70% {{ transform: scale(1.16); opacity: 1; }}
    100% {{ transform: scale(1); opacity: 1; }}
  }}
  @keyframes frs-loading-status-fade {{
    0%, 5% {{ opacity: 0; transform: translateY(3px); }}
    9%, 16% {{ opacity: 1; transform: translateY(0); }}
    20%, 100% {{ opacity: 0; transform: translateY(-3px); }}
  }}
  @keyframes frs-loading-status-final {{
    0% {{ opacity: 0; transform: translateY(3px); }}
    30%, 100% {{ opacity: 1; transform: translateY(0); }}
  }}
  @media (max-width: 640px) {{
    .frs-loading {{ min-height: 380px; }}
    .frs-loading-panel {{ padding: 22px 18px 18px; border-radius: 22px; }}
    .frs-loading-copy strong {{ font-size: 15px; }}
    .frs-loading-copy span {{ font-size: 13px; }}
  }}
 </style>
"""


def render_result_html(result: AnalysisResult) -> str:
    """Render the full result area from an ``AnalysisResult`` payload only."""

    if result.status is AnalysisStatus.COMPLETED:
        return _render_completed_html(result)
    return _render_status_html(result)


def _render_completed_html(result: AnalysisResult) -> str:
    banner = (
        '<div class="banner banner-info">'
        f'<p class="banner-title">分析完成 · 共 {len(result.findings)} 个风险</p>'
        "</div>"
    )
    findings = "".join(_render_finding_html(finding) for finding in result.findings)
    penalty_notice = (
        '<div class="banner banner-info" role="note">'
        '<p class="banner-note">'
        "仅作相关法律责任条款展示，不构成违法认定或处罚决定；"
        "是否满足处罚条件及具体行政处理，由有权机关结合现场事实依法认定。"
        "</p>"
        "</div>"
        if any(
            law.penalties
            for finding in result.findings
            for law in finding.legal_associations
        )
        else ""
    )
    return f'<div class="frs">{banner}{penalty_notice}{findings}</div>'


def _render_status_html(result: AnalysisResult) -> str:
    if result.status is AnalysisStatus.NO_FINDINGS:
        body = (
            f'<p class="banner-title">{_esc(result.message or "未发现明确消防风险。")}</p>'
            '<p class="banner-note">该结论仅基于图片可见内容，不代表现场全面合规。</p>'
        )
        return f'<div class="frs"><div class="banner banner-ok" role="status">{body}</div></div>'
    title, hint = _STATUS_META[result.status]
    message = _esc(result.message) if result.message else ""
    body = (
        f'<p class="banner-title">{_esc(title)}</p>'
        f"{f'<p class="banner-msg">{message}</p>' if message else ''}"
        f'<p class="banner-hint">{_esc(hint)}</p>'
    )
    return f'<div class="frs"><div class="banner banner-error" role="alert">{body}</div></div>'


def _render_finding_html(finding: AnalysisFinding) -> str:
    priority_label, priority_class = _PRIORITY_META.get(
        finding.risk_priority.value, (finding.risk_priority.value, "prio-low")
    )
    parts = [
        f'<section class="finding {priority_class}" id="finding-{_esc(finding.finding_id)}">',
        '<header class="finding-head">',
        f'<span class="finding-id">{_esc(finding.finding_id)}</span>',
        f'<h3 class="finding-title">{_esc(finding.title)}</h3>',
        f'<span class="prio {priority_class}">{_esc(priority_label)}</span>',
        "</header>",
    ]

    if finding.evidence:
        parts.append('<div class="sec"><span class="sec-label">可见证据</span><ul>')
        for evidence in finding.evidence:
            tag = (
                f'<span class="tag">标注 {_esc(finding.finding_id)}</span>'
                if evidence.bboxes
                else ""
            )
            parts.append(f"<li>{_esc(evidence.text)}{tag}</li>")
        parts.append("</ul></div>")

    parts.append('<div class="sec"><span class="sec-label">风险说明</span>')
    parts.append(f'<p class="desc">{_esc(finding.description)}</p>')
    parts.append(f'<p class="mech">风险机理：{_esc(finding.risk_mechanism)}</p>')
    parts.append("</div>")

    if finding.legal_associations:
        parts.append('<div class="sec"><span class="sec-label">相关法规</span>')
        parts.extend(_render_clause_html(law) for law in finding.legal_associations)
        parts.append("</div>")

    parts.append('<div class="sec"><span class="sec-label">整改建议</span>')
    parts.append(f"<p>{_esc(finding.recommended_action)}</p>")
    parts.append("</div>")

    if finding.limitations:
        parts.append('<div class="sec limits"><span class="sec-label">分析限制</span><ul>')
        parts.extend(f"<li>{_esc(item)}</li>" for item in finding.limitations)
        parts.append("</ul></div>")

    if finding.rule_warnings:
        parts.append('<div class="sec warns"><span class="sec-label">规则提示</span><ul>')
        parts.extend(f"<li>{_esc(item)}</li>" for item in finding.rule_warnings)
        parts.append("</ul></div>")

    parts.append("</section>")
    return "".join(parts)


def _render_clause_html(law: LegalAssociation) -> str:
    if law.relation is LegalRelation.DIRECT:
        rel_class, rel_text = "rel-direct", "直接相关"
    else:
        rel_class, rel_text = "rel-conditional", "条件相关"
    missing = "".join(f"<li>{_esc(item)}</li>" for item in law.missing_conditions)
    missing_ul = f'<ul class="missing-cond">{missing}</ul>' if missing else ""
    legal_html = (
        '<details class="clause">'
        "<summary>"
        f'<span class="clause-cite">《{_esc(law.source_name)}》 {_esc(law.clause_number)}</span>'
        f'<span class="rel {rel_class}">{rel_text}</span>'
        f'<code class="clause-id">{_esc(law.clause_id)}</code>'
        "</summary>"
        f'<p class="clause-text">{_esc(law.clause_text)}</p>'
        "</details>"
        f"{missing_ul}"
    )
    if not law.penalties:
        return legal_html
    penalties = "".join(_render_penalty_html(item) for item in law.penalties)
    return (
        f"{legal_html}"
        '<div class="sec">'
        '<span class="sec-label">相关处罚规定</span>'
        f"{penalties}"
        "</div>"
    )


def _render_penalty_html(penalty: PenaltyAssociation) -> str:
    conditions = "".join(f"<li>{_esc(item)}</li>" for item in penalty.missing_conditions)
    conditions_html = (
        '<ul class="missing-cond">'
        "<li><strong>处罚适用还需确认：</strong></li>"
        f"{conditions}"
        "</ul>"
        if conditions
        else ""
    )
    return (
        '<details class="clause">'
        "<summary>"
        f'<span class="clause-cite">《{_esc(penalty.source_name)}》 '
        f'{_esc(penalty.clause_number)}</span>'
        f'<code class="clause-id">{_esc(penalty.clause_id)}</code>'
        "</summary>"
        f'<p class="clause-text">{_esc(penalty.clause_text)}</p>'
        "</details>"
        f"{conditions_html}"
    )


def _annotated_for_result(prepared: PreparedImage, result: AnalysisResult) -> Image.Image | None:
    """Draw finding bboxes onto the prepared image, or None for error results."""

    if result.status not in (AnalysisStatus.COMPLETED, AnalysisStatus.NO_FINDINGS):
        return None
    bboxes: dict[str, list[object]] = {}
    for finding in result.findings:
        for evidence in finding.evidence:
            if evidence.bboxes:
                bboxes.setdefault(finding.finding_id, []).extend(evidence.bboxes)
    return draw_bboxes(prepared, bboxes)


async def _run_analysis_event(
    image_path: str | None, settings: Settings | None = None
) -> tuple[Image.Image | None, str]:
    """Wire a Gradio upload event to the pipeline and the result renderer."""

    app_settings = settings or get_settings()
    if not image_path:
        return None, _EMPTY_HINT_HTML
    try:
        prepared = prepare_image(image_path, app_settings)
    except ImageProcessingError as exc:
        result = AnalysisResult(status=AnalysisStatus.IMAGE_UNUSABLE, message=str(exc), findings=[])
        return None, render_result_html(result)
    try:
        result = await analyze(prepared, settings=app_settings)
        annotated = _annotated_for_result(prepared, result)
    except Exception as exc:  # UI 兜底：任何未预期异常都不崩页
        result = AnalysisResult(
            status=AnalysisStatus.MODEL_FAILED,
            message=f"系统分析异常：{exc}",
            findings=[],
        )
        return None, render_result_html(result)
    return annotated, render_result_html(result)


def build_app(settings: Settings | None = None) -> gr.Blocks:
    """Build and return the Gradio application without starting a server."""

    app_settings = settings or get_settings()

    # Gradio 6: theme and css are launch-time parameters; see launch_app.
    with gr.Blocks(
        analytics_enabled=False,
        title="消防风险分析系统",
    ) as app:
        gr.HTML(_TOP_BANNER_HTML, elem_id="top_banner")
        gr.Markdown("# **智消慧检**\n\n基于多模态人工智能的消防安全风险智能识别与辅助研判系统")
        gr.Markdown(f"分析模型：{app_settings.qwen_model}")

        with gr.Row():
            with gr.Column(scale=5, min_width=360):
                image_input = gr.Image(
                    label="上传消防场景图片",
                    type="filepath",
                    sources=["upload", "clipboard"],
                    elem_id="source_image",
                )
                with gr.Row():
                    analyze_button = gr.Button("开始分析", variant="primary")
                    clear_button = gr.ClearButton(image_input, value="清空")
                annotated_output = gr.Image(
                    label="风险标注结果",
                    interactive=False,
                    buttons=["download"],
                )
            with gr.Column(scale=7, min_width=380):
                result_area = gr.HTML(_EMPTY_HINT_HTML, elem_id="result_area")

        async def on_analyze(image_path: str | None) -> tuple[Image.Image | None, str]:
            return await _run_analysis_event(image_path, app_settings)

        def clear_analysis_outputs() -> tuple[None, str]:
            """Clear results whenever the input image changes or is cleared."""

            return None, _EMPTY_HINT_HTML

        # show_progress="hidden": 关闭 Gradio 的队列进度遮罩与右下角
        # "processing | 已用时/预估总时长" 计时器；加载期间的反馈由页面自带的
        # 六阶段视觉加载态承担（见 render_loading_html）。
        analysis_event = analyze_button.click(
            render_loading_html,
            outputs=result_area,
            show_progress="hidden",
            js=_START_SCAN_JS,
        ).then(
            on_analyze,
            inputs=image_input,
            outputs=[annotated_output, result_area],
            show_progress="hidden",
        )
        analysis_event.then(fn=None, js=_STOP_SCAN_JS)
        image_input.change(
            clear_analysis_outputs,
            outputs=[annotated_output, result_area],
            show_progress="hidden",
            js=_STOP_SCAN_JS,
        )
        clear_button.click(
            clear_analysis_outputs,
            outputs=[annotated_output, result_area],
            show_progress="hidden",
            js=_STOP_SCAN_JS,
        )

    return app


def launch_app(settings: Settings | None = None, **launch_kwargs: Any) -> None:
    """Build and launch the Gradio application."""

    app_settings = settings or get_settings()
    _ensure_localhost_proxy_bypass()
    build_app(app_settings).launch(
        server_name=app_settings.host,
        server_port=app_settings.port,
        theme=_THEME,
        css=_CSS,
        footer_links=[],  # 隐藏 Gradio 底栏:通过 API 使用 / 使用 Gradio 构建 / 设置
        **launch_kwargs,
    )


def _ensure_localhost_proxy_bypass() -> None:
    """Keep Gradio's local startup self-check out of configured proxies."""

    required_hosts = ("127.0.0.1", "localhost")
    for variable in ("NO_PROXY", "no_proxy"):
        entries = [
            entry.strip()
            for entry in os.environ.get(variable, "").split(",")
            if entry.strip()
        ]
        normalized = {entry.lower() for entry in entries}
        for host in required_hosts:
            if host not in normalized:
                entries.append(host)
                normalized.add(host)
        os.environ[variable] = ",".join(entries)


__all__ = [
    "build_app",
    "launch_app",
    "render_empty_html",
    "render_loading_html",
    "render_result_html",
]
