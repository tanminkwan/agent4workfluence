"""상위 tool: step을 순서대로 실행한다 (설계 6.2절, FR-306). 분기·반복은 없고 정해진 기능은 expect뿐이다."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from .errors import AMBIGUOUS, NOT_FOUND, STEP_FAILED, UNSUPPORTED, ToolError, ToolResult
from .pointer import PointerMissing, resolve

if TYPE_CHECKING:
    from .executor import ToolExecutor

CANDIDATES_MAX = 10


def run_composite(
    executor: "ToolExecutor",
    definition: Mapping[str, Any],
    args: Mapping[str, Any],
    toolset: Mapping[str, Mapping[str, Any]],
    context: Mapping[str, Any] | None = None,
) -> ToolResult:
    results: dict[str, Any] = {}
    steps_meta: list[dict[str, Any]] = []

    def fail(error: ToolError, data: Any = None) -> ToolResult:
        return ToolResult(ok=False, data=data, error=error, meta={"steps": steps_meta})

    for step in definition["steps"]:
        sid = step["id"]
        target = toolset.get(step["tool"])
        if target is None or target.get("kind") != "operation":
            return fail(ToolError(UNSUPPORTED, f"step {sid}: 묶음에 없는 단순 tool {step['tool']}", step=sid))
        try:
            step_args = _resolve_args(step["args"], args, results)
        except PointerMissing as e:
            return fail(ToolError(NOT_FOUND, f"step {sid}: 앞 step 결과에 {e} 값이 없다", step=sid))
        result = executor.execute(target, step_args, context=context)
        steps_meta.append({"id": sid, "tool": step["tool"], "ok": result.ok, "status": result.meta.get("status")})
        if not result.ok:
            inner = result.error
            return fail(
                ToolError(
                    STEP_FAILED,
                    f"step {sid}({step['tool']}) 실패: {inner.message}",
                    target_code=inner.target_code,
                    request_id=inner.request_id,
                    http_status=inner.http_status,
                    retryable=inner.retryable,
                    hint=inner.hint,
                    step=sid,
                    details={"code": inner.code, "details": inner.details},
                )
            )
        expect = step.get("expect")
        if expect:
            try:
                items = resolve(result.data, expect["pointer"])
            except PointerMissing:
                items = []
            items = items if isinstance(items, list) else []
            if not items:
                return fail(ToolError(NOT_FOUND, f"step {sid}: 찾은 것이 없다", step=sid,
                                      hint="조건을 바꾸거나 넓혀 다시 하라"))
            if expect["count"] == "one" and len(items) > 1:
                return fail(
                    ToolError(AMBIGUOUS, f"step {sid}: {len(items)}개가 맞는다. 하나로 좁혀야 한다", step=sid,
                              hint="candidates 중 하나를 골라 조건을 좁혀 다시 하라"),
                    data={"candidates": items[:CANDIDATES_MAX]},
                )
        results[sid] = result.data

    output = definition.get("output")
    if output is None:
        data = results[definition["steps"][-1]["id"]]
    else:
        data = resolve(results[output["$step"]], output.get("pointer", ""))
    return ToolResult(ok=True, data=data, meta={"steps": steps_meta})


def _resolve_args(spec: Mapping[str, Any], inputs: Mapping[str, Any], results: Mapping[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name, ref in spec.items():
        if "$input" in ref:
            value = inputs.get(ref["$input"])
            if value is None:
                continue  # 비운 선택 칸은 보내지 않는다
            out[name] = value
        elif "$step" in ref:
            out[name] = resolve(results[ref["$step"]], ref.get("pointer", ""))
        else:
            out[name] = ref["$const"]
    return out
