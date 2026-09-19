"""Per-request stage timing for one analysis run.

The analysis path is instrumented with :func:`stage` blocks. Recording is
opt-in: without an active :class:`TimingRecorder`, :func:`stage` does nothing.
That keeps timing out of every public signature — the pipeline and the Qwen
client never take a timing argument, and their test doubles stay unchanged —
while ``ui._run_analysis_event`` still collects one breakdown per click.

Stages are measured independently and never nest, so one recorded run reads as
a chronological breakdown: 图片预处理, 模型请求, and 后续处理, the three
segments a reader of the result page sees.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from time import perf_counter

# 跨模块共享的阶段名：模型返回之后的全部工作——响应解析与校验、Finding 清洗、
# Issue Code 与法规关联、标注图绘制、结果渲染——是同一步骤的若干段，分别落在
# qwen.py、pipeline.py 和 ui.py。名字相同且相邻的记录会被合并成一行，使这一步骤
# 在面板和日志里仍是一段连续测量。
POST_MODEL_STAGE = "后续处理"


@dataclass(frozen=True)
class StageTiming:
    """One measured span inside a single analysis run."""

    name: str
    seconds: float


@dataclass(frozen=True)
class TimingReport:
    """Frozen snapshot of a run's stages, taken once the measured work is done."""

    total_seconds: float
    stages: tuple[StageTiming, ...]

    def summary(self) -> str:
        """Return a single-line, grep-able breakdown in recorded order."""

        stages = " ".join(f"{item.name}={item.seconds:.3f}s" for item in self.stages)
        return f"total={self.total_seconds:.3f}s" + (f" {stages}" if stages else "")


class TimingRecorder:
    """Collects non-overlapping stage durations for one analysis run."""

    def __init__(self) -> None:
        self._stages: list[StageTiming] = []
        self._started_at = perf_counter()

    def record(self, name: str, seconds: float) -> None:
        """Append one already-measured stage, merging it into the previous one.

        Stages that share a name and run back to back are one step split across
        module boundaries; summing them keeps the breakdown aligned with the
        steps a reader recognizes instead of emitting near-zero rows.
        """

        if self._stages and self._stages[-1].name == name:
            previous = self._stages[-1]
            self._stages[-1] = StageTiming(name=name, seconds=previous.seconds + seconds)
            return
        self._stages.append(StageTiming(name=name, seconds=seconds))

    @property
    def stages(self) -> tuple[StageTiming, ...]:
        """Stages in recorded — that is, chronological — order."""

        return tuple(self._stages)

    @contextmanager
    def stage(self, name: str) -> Iterator[None]:
        """Measure the enclosed block, recording it even when it raises."""

        started_at = perf_counter()
        try:
            yield
        finally:
            self.record(name, perf_counter() - started_at)

    def snapshot(self) -> TimingReport:
        """Freeze the stages recorded so far together with the elapsed total."""

        return TimingReport(
            total_seconds=perf_counter() - self._started_at,
            stages=tuple(self._stages),
        )


_active_recorder: ContextVar[TimingRecorder | None] = ContextVar(
    "fire_safety_timing_recorder", default=None
)


def active_recorder() -> TimingRecorder | None:
    """Return the recorder active in the current context, if any."""

    return _active_recorder.get()


@contextmanager
def recording(recorder: TimingRecorder) -> Iterator[TimingRecorder]:
    """Activate ``recorder`` for the duration of the ``with`` block.

    The recorder is held in a :class:`~contextvars.ContextVar` rather than in a
    module global, so overlapping runs — Gradio serves concurrent events —
    never record into each other's breakdown.
    """

    token = _active_recorder.set(recorder)
    try:
        yield recorder
    finally:
        _active_recorder.reset(token)


@contextmanager
def stage(name: str) -> Iterator[None]:
    """Measure one named stage when a recorder is active; otherwise do nothing."""

    recorder = _active_recorder.get()
    if recorder is None:
        yield
        return
    with recorder.stage(name):
        yield


__all__ = [
    "POST_MODEL_STAGE",
    "StageTiming",
    "TimingRecorder",
    "TimingReport",
    "active_recorder",
    "recording",
    "stage",
]
