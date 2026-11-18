"""指标口径的离线测试。"""

import json
from pathlib import Path

import pytest

from pop_engine.metrics import summarize
from pop_engine.types import StepEvent


def test_empty_events() -> None:
    assert summarize([]) == {"model_calls": 0.0, "total_ms": 0.0, "steps": 0.0}


def test_mixed_events_and_input_unchanged() -> None:
    events = [
        StepEvent(kind="model", step=1, duration_ms=10.25),
        StepEvent(kind="tool_start", step=1),
        StepEvent(kind="tool_end", step=1, duration_ms=2.5),
        StepEvent(kind="model", step=2, duration_ms=0.0),
    ]
    original = tuple(events)

    assert summarize(events) == {"model_calls": 2.0, "total_ms": 12.75, "steps": 2.0}
    assert tuple(events) == original


def test_failed_model_call_is_counted() -> None:
    events = (StepEvent(kind="model", step=1, duration_ms=3.5, error="timeout"),)
    assert summarize(events) == {"model_calls": 1.0, "total_ms": 3.5, "steps": 1.0}


def test_counts_events_not_step_numbers() -> None:
    # 子运行可能重新编号，输入也可能不是按 step 排序的；统计不依赖编号。
    events = (
        StepEvent(kind="model", step=8),
        StepEvent(kind="model", step=1),
        StepEvent(kind="model", step=1),
        StepEvent(kind="tool_end", step=99, duration_ms=2.0),
    )
    assert summarize(events) == {"model_calls": 3.0, "total_ms": 2.0, "steps": 3.0}


def test_tools_are_not_model_steps() -> None:
    events = (
        StepEvent(kind="tool_start", step=1),
        StepEvent(kind="tool_end", step=1, duration_ms=1.25),
    )
    assert summarize(events) == {"model_calls": 0.0, "total_ms": 1.25, "steps": 0.0}


def test_recorded_m2_events() -> None:
    evidence = Path(__file__).resolve().parents[1] / "docs/evidence/m2-run-ok.jsonl"
    events = [
        StepEvent(**json.loads(line))
        for line in evidence.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    result = summarize(events)

    assert result["model_calls"] == 6.0
    assert result["steps"] == 6.0
    assert result["total_ms"] == pytest.approx(25644.21)
