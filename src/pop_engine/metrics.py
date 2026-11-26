"""从运行事件汇总指标，不依赖引擎控制流或文件格式"""

from collections.abc import Sequence

from pop_engine.types import StepEvent


def summarize(events: Sequence[StepEvent]) -> dict[str, float]:
    """汇总模型调用次数、事件累计耗时和模型步数。

    model_calls 每次模型调用计一次，失败的也算。

    total_ms是所有非空duration_ms之和，不是墙钟耗时

    steps保留RunResult.steps的模型调用次数语义，因此与model_calls相同，
    不使用事件step的最大值，也不将工具事件或者graph节点当作模型的步数
    """

    model_calls = float(sum(event.kind == "model" for event in events))

    total_ms = sum((event.duration_ms or 0.0) for event in events)

    return {"model_calls": model_calls, "total_ms": total_ms, "steps": model_calls}
