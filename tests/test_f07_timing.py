from __future__ import annotations

import asyncio
import logging
from io import BytesIO
from types import SimpleNamespace

import pytest
from conftest import VALID_VISUAL_OUTPUT
from PIL import Image
from test_f03_qwen import FakeClient, FakeCompletions, configured_settings

import fire_safety.ui as ui
from fire_safety.image import PreparedImage, prepare_image
from fire_safety.pipeline import analyze
from fire_safety.qwen import analyze_image
from fire_safety.rules import load_rule_catalog
from fire_safety.schemas import AnalysisResult, AnalysisStatus, VisualInvestigation
from fire_safety.settings import Settings
from fire_safety.timing import (
    POST_MODEL_STAGE,
    StageTiming,
    TimingNote,
    TimingRecorder,
    TimingReport,
    active_recorder,
    note,
    recording,
    stage,
)


def png_bytes() -> bytes:
    output = BytesIO()
    Image.new("RGB", (40, 30), color=(30, 60, 90)).save(output, format="PNG")
    return output.getvalue()


def prepared_image() -> PreparedImage:
    return prepare_image(png_bytes())


def stage_names(recorder: TimingRecorder) -> list[str]:
    return [item.name for item in recorder.stages]


async def fake_qwen(image: PreparedImage, *, settings: Settings | None = None) -> object:
    return VisualInvestigation.model_validate(VALID_VISUAL_OUTPUT)


async def stub_analyze(prepared: object, *, settings: Settings | None = None) -> AnalysisResult:
    """Stand-in for the instrumented pipeline: the event handler owns the recorder."""

    # 与真实链路一样：准备 + 请求是相邻的两段同名记录，返回后的解析另起一段。
    with stage("模型请求"):
        pass
    with stage("模型请求"):
        pass
    note("ttfb", "1.234s")
    note("ttft", "1.345s")
    note("prompt_tokens", 4250)
    with stage(POST_MODEL_STAGE):
        return AnalysisResult(status=AnalysisStatus.COMPLETED, findings=[])


async def run_analysis_event(image_path: str, settings: Settings | None = None):
    return await ui._run_analysis_event(image_path, settings)


def test_stage_without_recorder_is_a_noop() -> None:
    """Instrumented production code runs unchanged when nothing is recording."""

    assert active_recorder() is None

    with stage("无人监听"):
        pass

    assert active_recorder() is None


def test_recorder_collects_stages_in_order() -> None:
    recorder = TimingRecorder()
    with recording(recorder):
        with stage("第一步"):
            pass
        with stage("第二步"):
            pass

    assert stage_names(recorder) == ["第一步", "第二步"]
    report = recorder.snapshot()
    assert report.total_seconds >= sum(item.seconds for item in report.stages)


def test_stage_is_recorded_when_the_block_raises() -> None:
    recorder = TimingRecorder()
    with recording(recorder), pytest.raises(RuntimeError):
        with stage("失败阶段"):
            raise RuntimeError("boom")

    assert stage_names(recorder) == ["失败阶段"]


def test_consecutive_same_named_stages_merge_into_one() -> None:
    """One step instrumented in two modules must read as one segment."""

    recorder = TimingRecorder()
    with recording(recorder):
        with stage(POST_MODEL_STAGE):
            pass
        with stage(POST_MODEL_STAGE):
            pass
        with stage("结果渲染"):
            pass

    assert stage_names(recorder) == [POST_MODEL_STAGE, "结果渲染"]


def test_recorders_do_not_share_stages_between_concurrent_runs() -> None:
    """Gradio serves concurrent events; one click must not record into another."""

    async def run(name: str, recorder: TimingRecorder, delay: float) -> None:
        with recording(recorder):
            with stage(name):
                await asyncio.sleep(delay)

    async def main() -> tuple[TimingRecorder, TimingRecorder]:
        first, second = TimingRecorder(), TimingRecorder()
        await asyncio.gather(run("甲", first, 0.02), run("乙", second, 0.01))
        return first, second

    first, second = asyncio.run(main())

    assert stage_names(first) == ["甲"]
    assert stage_names(second) == ["乙"]


def test_report_summary_lists_stages_notes_and_total() -> None:
    report = TimingReport(
        total_seconds=1.5,
        stages=(StageTiming(name="模型请求", seconds=1.25),),
        notes=(TimingNote(name="ttft", value="0.500s"),),
    )

    summary = report.summary()

    assert summary.startswith("total=1.500s")
    assert "模型请求=1.250s" in summary
    assert "ttft=0.500s" in summary


def test_pipeline_keeps_post_model_work_in_one_stage() -> None:
    """Parsing, cleanup, and rule resolution are one segment, not three rows."""

    async def parsing_qwen(image: PreparedImage, *, settings: Settings | None = None) -> object:
        with stage(POST_MODEL_STAGE):
            pass
        return VisualInvestigation.model_validate(VALID_VISUAL_OUTPUT)

    recorder = TimingRecorder()
    with recording(recorder):
        result = asyncio.run(
            analyze(
                prepared_image(),
                qwen_analyzer=parsing_qwen,
                rule_catalog=load_rule_catalog(),
            )
        )

    assert result.status is AnalysisStatus.COMPLETED
    assert stage_names(recorder) == [POST_MODEL_STAGE]
    # 已准备的图片不再重复计时：图片预处理属于调用方。
    assert "图片预处理" not in stage_names(recorder)


def test_pipeline_records_preparation_for_raw_bytes() -> None:
    recorder = TimingRecorder()
    with recording(recorder):
        asyncio.run(analyze(png_bytes(), qwen_analyzer=fake_qwen, rule_catalog=load_rule_catalog()))

    assert "图片预处理" in stage_names(recorder)


def test_qwen_reports_setup_and_request_as_one_stage() -> None:
    """准备与请求合成一段「模型请求」，返回值解析属于「后续处理」。"""

    completions = FakeCompletions()

    recorder = TimingRecorder()
    with recording(recorder):
        asyncio.run(
            analyze_image(
                prepared_image(),
                settings=configured_settings(),
                client=FakeClient(completions),  # type: ignore[arg-type]
            )
        )

    assert stage_names(recorder) == ["模型请求", POST_MODEL_STAGE]
    assert len(completions.calls) == 1


def test_qwen_records_usage_and_first_token_latency() -> None:
    """Token accounting and 首包延迟 ride along as notes, not as stages."""

    usage = SimpleNamespace(
        prompt_tokens=4250,
        completion_tokens=2355,
        completion_tokens_details=SimpleNamespace(reasoning_tokens=1151),
    )

    recorder = TimingRecorder()
    with recording(recorder):
        asyncio.run(
            analyze_image(
                prepared_image(),
                settings=configured_settings(),
                client=FakeClient(FakeCompletions(usage=usage)),  # type: ignore[arg-type]
            )
        )

    notes = {item.name: item.value for item in recorder.notes}
    assert set(notes) == {
        "ttfb",
        "ttft",
        "prompt_tokens",
        "completion_tokens",
        "reasoning_tokens",
    }
    assert notes["prompt_tokens"] == 4250
    assert notes["completion_tokens"] == 2355
    assert notes["reasoning_tokens"] == 1151
    assert str(notes["ttfb"]).endswith("s")


def test_qwen_records_no_token_notes_without_usage() -> None:
    recorder = TimingRecorder()
    with recording(recorder):
        asyncio.run(
            analyze_image(
                prepared_image(),
                settings=configured_settings(),
                client=FakeClient(FakeCompletions()),  # type: ignore[arg-type]
            )
        )

    assert [item.name for item in recorder.notes] == ["ttfb", "ttft"]


def test_notes_are_noops_without_a_recorder() -> None:
    note("ttft", "1.000s")

    assert active_recorder() is None


def test_render_timing_html_lists_one_row_per_stage() -> None:
    report = TimingReport(
        total_seconds=42.0,
        stages=(
            StageTiming(name="图片预处理", seconds=0.5),
            StageTiming(name="模型请求", seconds=41.0),
            StageTiming(name=POST_MODEL_STAGE, seconds=0.5),
        ),
    )

    rendered = ui.render_timing_html(report)

    assert '<details class="timing">' in rendered
    assert "本次耗时" in rendered
    assert "后端 42.00 s" in rendered
    assert rendered.count('class="timing-row"') == 3
    assert "模型请求" in rendered
    assert "41.00 s" in rendered
    assert "500 ms" in rendered
    assert "97.6%" in rendered
    assert 'id="frs-timing-client-total"' in rendered


def test_render_timing_html_without_report_is_empty() -> None:
    assert ui.render_timing_html() == ""


def test_analysis_event_appends_timing_panel_and_logs_it(tmp_path, monkeypatch, caplog) -> None:
    image_path = tmp_path / "scene.png"
    image_path.write_bytes(png_bytes())
    monkeypatch.setattr(ui, "analyze", stub_analyze)

    with caplog.at_level(logging.INFO, logger=ui.__name__):
        annotated, result_html = asyncio.run(run_analysis_event(str(image_path), Settings()))

    assert annotated is not None
    assert "本次耗时" in result_html
    assert 'id="frs-timing-client-total"' in result_html
    assert "图片预处理" in result_html
    assert "模型请求" in result_html
    assert POST_MODEL_STAGE in result_html
    assert result_html.count('class="timing-row"') == 3
    log_line = next(
        record.getMessage() for record in caplog.records if "[耗时]" in record.getMessage()
    )
    assert "status=completed" in log_line
    assert "图片预处理=" in log_line
    assert "ttfb=1.234s" in log_line
    assert "prompt_tokens=4250" in log_line


def test_analysis_event_reports_timing_when_the_image_is_unusable(tmp_path) -> None:
    image_path = tmp_path / "broken.png"
    image_path.write_bytes(b"not an image")

    annotated, result_html = asyncio.run(run_analysis_event(str(image_path), Settings()))

    assert annotated is None
    assert "图片无法使用" in result_html
    assert "本次耗时" in result_html
    assert "图片预处理" in result_html


def test_browser_reports_click_to_paint_elapsed() -> None:
    app = ui.build_app()
    dependencies = app.config["dependencies"]

    stamped = [
        item
        for item in dependencies
        if "__frsClickAt = performance.now()" in (item.get("js") or "")
    ]
    reported = [
        item for item in dependencies if "frs-timing-client-total" in (item.get("js") or "")
    ]

    assert len(stamped) == 1
    assert len(reported) == 1
    assert 'classList.remove("frs-scanning")' in reported[0]["js"]
