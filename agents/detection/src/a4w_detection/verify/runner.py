"""검증 실행 (Phase 2 설계 11.1절): 시험 환경 준비 → 읽기 → 쓰기 → 상위 tool → 정리."""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any

from a4w_tools.executor import ToolExecutor
from a4w_tools.pointer import PointerMissing, resolve
from a4w_tools.request import READ_METHODS
from a4w_tools.sandbox import RawCaller, SandboxContext, VerifySandbox

from .auto import auto_scenario
from .scenarios import SandboxValueMissing, ScenarioError, fill

PASSED, FAILED, UNVERIFIED, SKIPPED = "passed", "failed", "unverified", "skipped"


class VerificationRunner:
    def __init__(self, *, executor: ToolExecutor, caller: RawCaller, sandbox: VerifySandbox):
        self._executor = executor
        self._caller = caller
        self.sandbox = sandbox

    def run(
        self, verification_id: str, *, tools: list[dict], references: list[dict], scenarios: Mapping[str, Any]
    ) -> dict[str, Any]:
        ctx = self.sandbox.prepare(self._caller, verification_id)  # 실패하면 SandboxUnavailable
        toolset = {d["name"]: d for d in list(references) + list(tools)}
        results: dict[str, dict[str, Any]] = {}
        try:
            for tool in sorted(tools, key=_order):
                results[tool["name"]] = self._run_one(tool, toolset, scenarios.get(tool["name"]), ctx,
                                                      verification_id, results)
        finally:
            report = self.sandbox.cleanup(self._caller, ctx)
        return {
            "results": [results[t["name"]] for t in tools],
            "sandbox": {"prepared": True, "cleanup": "ok" if report.ok else "failed",
                        "cleanupFailures": list(report.failures)},
        }

    def _run_one(self, tool, toolset, scenario, ctx: SandboxContext, run_id, results) -> dict[str, Any]:
        started = time.monotonic()
        calls = 0

        def done(result: str, reason: str | None = None, error: dict | None = None) -> dict[str, Any]:
            out = {"tool": tool["name"], "revision": tool["revision"], "result": result, "calls": calls,
                   "elapsedMs": int((time.monotonic() - started) * 1000)}
            if reason:
                out["reason"] = reason
            if error:
                out["error"] = error
            return out

        if tool["kind"] == "composite":
            broken = [s["tool"] for s in tool["steps"] if results.get(s["tool"], {}).get("result") == FAILED]
            if broken:
                return done(SKIPPED, "REFERENCE_FAILED", {"message": f"참조 tool 실패: {', '.join(broken)}"})
        if scenario is None:
            scenario = auto_scenario(tool, ctx.values)
        if scenario is None:
            return done(UNVERIFIED, "NO_SCENARIO")

        variables: dict[str, Any] = {"sandbox": ctx.values, "run": {"id": run_id}, "saved": {}, "setup": {}}
        try:
            for i, item in enumerate(scenario.get("setup", [])):
                r = self._executor.execute(toolset[item["tool"]], fill(item.get("args", {}), variables), toolset=toolset,
                                           context=ctx.values)
                calls += 1
                if not r.ok:
                    return done(FAILED, "SETUP_FAILED", r.error.to_dict())
                variables["setup"][str(i)] = r.data
                for key, pointer in item.get("save", {}).items():
                    variables["saved"][key] = resolve(r.data, pointer)
            args = fill(scenario.get("call", {}).get("args", {}), variables)
        except SandboxValueMissing as e:
            # 맥락 키는 guard가 확인했다. 값이 없으면 이 환경이 그 값을 못 만든 것이지 tool의 잘못이 아니다 (11.6.1)
            return done(UNVERIFIED, "SANDBOX_VALUE_MISSING", {"message": str(e)})
        except (ScenarioError, PointerMissing) as e:
            return done(FAILED, "SCENARIO_INVALID", {"message": str(e)})

        # 시험 환경 값이 실행 맥락이다 (맥락 키로 고정된 칸도 시험 스페이스 안에 머문다)
        r = self._executor.execute(tool, args, toolset=toolset, context=ctx.values)
        calls += len(r.meta.get("steps", [])) or 1
        expect = scenario.get("expect", {"ok": True})
        if expect.get("ok", True):
            if not r.ok:
                return done(FAILED, "TOOL_FAILED", r.error.to_dict())
            missing = [f for f in expect.get("fields", []) if not _has_field(r.data, f)]
            if missing:
                return done(FAILED, "EXPECT_FIELD_MISSING", {"message": f"결과에 없는 필드: {', '.join(missing)}"})
            return done(PASSED)
        if r.ok:
            return done(FAILED, "EXPECT_ERROR_MISSING", {"message": "실패를 기대했는데 성공했다"})
        wanted = expect.get("error")
        if wanted and wanted not in (r.error.target_code, r.error.code):
            return done(FAILED, "EXPECT_ERROR_MISMATCH", r.error.to_dict())
        return done(PASSED)


def _order(tool: Mapping[str, Any]) -> int:
    if tool["kind"] == "operation":
        return 0 if tool["http"]["method"] in READ_METHODS else 1
    return 2


def _has_field(data: Any, path: str) -> bool:
    node = data
    for part in path.split("."):
        if part.endswith("[]"):
            name = part[:-2]
            if name:
                if not isinstance(node, Mapping) or name not in node:
                    return False
                node = node[name]
            if not isinstance(node, list) or not node:
                return False
            node = node[0]
        elif isinstance(node, Mapping) and part in node:
            node = node[part]
        else:
            return False
    return True
