import os

import fire_safety.ui as ui
from fire_safety.settings import Settings
from fire_safety.ui import _render_top_banner_html, build_app, render_loading_html


def test_f01_builds_gradio_app() -> None:
    app = build_app()

    assert app is not None


def test_launch_app_adds_localhost_to_proxy_bypass(monkeypatch) -> None:
    """The Gradio startup self-check must not be sent through a local proxy."""

    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)

    class FakeApp:
        def launch(self, **kwargs):
            return None

    monkeypatch.setattr(ui, "build_app", lambda settings: FakeApp())

    ui.launch_app(Settings())

    assert "127.0.0.1" in os.environ.get("NO_PROXY", "")
    assert "localhost" in os.environ.get("NO_PROXY", "")
    assert "127.0.0.1" in os.environ.get("no_proxy", "")
    assert "localhost" in os.environ.get("no_proxy", "")


def test_loading_state_renders_six_visual_stages() -> None:
    loading = render_loading_html()

    assert loading.count('class="frs-loading-stage"') == 6
    assert "图像预处理" in loading
    assert "现场视觉理解" in loading
    assert "风险识别" in loading
    assert "场景风险评估" in loading
    assert "规则与法规关联" in loading
    assert "结果校验" in loading
    assert "frs-loading-breathe 5s" in loading
    assert "frs-loading-check" in loading
    assert "frs-loading-status-fade" in loading
    assert "frs-loading-status-fade 30s" in loading
    assert ".frs-loading-stage:nth-child(6) { animation-delay: 25s; }" in loading
    assert ".frs-loading-heading {" in loading
    assert "color: var(--loading-ink) !important;" in loading
    assert ".frs-loading-status > span {" in loading
    assert "color: var(--loading-muted) !important;" in loading
    assert "--loading-panel: #ffffff;" in loading
    assert "--loading-ink: #0f172a;" in loading
    assert "@media (prefers-color-scheme: dark)" in loading
    assert "--loading-panel: #202224;" in loading
    assert "0%, 8% { transform: scale(1); opacity: .95;" in loading


def test_top_banner_renders_the_branding_asset() -> None:
    banner = _render_top_banner_html()

    assert 'class="frs-top-banner"' in banner
    assert 'alt="佛山市消防救援局' in banner
    assert "data:image/png;base64," in banner


def test_upload_change_clears_previous_analysis_outputs() -> None:
    app = build_app()

    components = {component_id: component for component_id, component in app.blocks.items()}
    image_input_id = next(
        component_id
        for component_id, component in components.items()
        if getattr(component, "label", None) == "上传消防场景图片"
    )
    annotated_output_id = next(
        component_id
        for component_id, component in components.items()
        if getattr(component, "label", None) == "风险标注结果"
    )
    result_area_id = next(
        component_id
        for component_id, component in components.items()
        if getattr(component, "elem_id", None) == "result_area"
    )

    upload_events = [
        dependency
        for dependency in app.config["dependencies"]
        if (image_input_id, "change") in dependency.get("targets", [])
    ]

    assert len(upload_events) == 1
    assert upload_events[0]["outputs"] == [annotated_output_id, result_area_id]


def test_analysis_controls_source_image_scan_animation() -> None:
    app = build_app()

    source_image = next(
        component
        for component in app.blocks.values()
        if getattr(component, "elem_id", None) == "source_image"
    )
    dependencies = app.config["dependencies"]
    scan_start = [
        dependency
        for dependency in dependencies
        if 'classList.add("frs-scanning")' in (dependency.get("js") or "")
    ]
    scan_stops = [
        dependency
        for dependency in dependencies
        if 'classList.remove("frs-scanning")' in (dependency.get("js") or "")
    ]

    assert source_image.label == "上传消防场景图片"
    assert len(scan_start) == 1
    assert len(scan_stops) == 3
