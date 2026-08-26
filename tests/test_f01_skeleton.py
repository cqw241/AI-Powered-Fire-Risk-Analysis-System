from fire_safety.ui import _render_top_banner_html, build_app, render_loading_html


def test_f01_builds_gradio_app() -> None:
    app = build_app()

    assert app is not None


def test_loading_state_renders_six_visual_stages() -> None:
    loading = render_loading_html()

    assert loading.count('class="frs-loading-stage"') == 6
    assert "图像预处理" in loading
    assert "现场视觉理解" in loading
    assert "风险识别" in loading
    assert "场景风险评估" in loading
    assert "规则与法规关联" in loading
    assert "结果校验" in loading
    assert "frs-loading-breathe 1.5s" in loading
    assert "frs-loading-check" in loading
    assert "frs-loading-status-fade" in loading
    assert "frs-loading-status-fade 30s" in loading
    assert ".frs-loading-stage:nth-child(6) { animation-delay: 25s; }" in loading


def test_top_banner_renders_the_branding_asset() -> None:
    banner = _render_top_banner_html()

    assert 'class="frs-top-banner"' in banner
    assert 'alt="佛山市消防救援局' in banner
    assert "data:image/png;base64," in banner


def test_upload_change_clears_previous_analysis_outputs() -> None:
    app = build_app()

    components = {
        component_id: component
        for component_id, component in app.blocks.items()
    }
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
