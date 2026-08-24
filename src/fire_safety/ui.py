"""Gradio page skeleton for the fire risk analysis system."""

from typing import Any

import gradio as gr

from fire_safety.settings import Settings, get_settings


def _analysis_placeholder(image_path: str | None) -> str:
    """Return the F01 placeholder state without performing analysis."""

    if not image_path:
        return "请先上传一张 JPEG、PNG 或 WEBP 图片。"

    return (
        "### F01 工程骨架已就绪\n\n"
        "图片已接收；图片处理、Qwen 分析、结构化校验和法规关联将在后续 Feature 接入。"
    )


def build_app(settings: Settings | None = None) -> gr.Blocks:
    """Build and return the Gradio application without starting a server."""

    app_settings = settings or get_settings()

    with gr.Blocks(
        analytics_enabled=False,
        title="消防风险分析系统",
    ) as app:
        gr.Markdown(
            "# Qwen3.8-27B 消防风险分析系统\n"
            "F01 工程和 Gradio 页面骨架"
        )

        with gr.Row():
            with gr.Column(scale=1):
                image_input = gr.Image(
                    label="上传消防场景图片",
                    type="filepath",
                    sources=["upload", "clipboard"],
                )
                analyze_button = gr.Button("开始分析", variant="primary")
            with gr.Column(scale=1):
                result_output = gr.Markdown(
                    "上传图片后点击“开始分析”。当前为 F01 页面骨架，尚未接入模型。"
                )

        gr.Markdown(
            f"当前模型配置：`{app_settings.qwen_model}`（F03 接入前仅展示配置，不发起请求）"
        )
        analyze_button.click(
            fn=_analysis_placeholder,
            inputs=image_input,
            outputs=result_output,
        )

    return app


def launch_app(settings: Settings | None = None, **launch_kwargs: Any) -> None:
    """Build and launch the Gradio application."""

    app_settings = settings or get_settings()
    build_app(app_settings).launch(
        server_name=app_settings.host,
        server_port=app_settings.port,
        **launch_kwargs,
    )
