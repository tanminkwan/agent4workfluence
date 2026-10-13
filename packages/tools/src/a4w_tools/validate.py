"""tool 정의·manifest 스키마 검사와 상위 tool 참조 무결성."""

from __future__ import annotations

import json
from collections.abc import Mapping
from functools import cache
from importlib.resources import files
from typing import Any

from jsonschema import Draft202012Validator


@cache
def _validator(name: str) -> Draft202012Validator:
    schema = json.loads(files("a4w_tools").joinpath("schemas", name).read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def schema_document(name: str) -> dict[str, Any]:
    """계약 스키마 원문 (`tool.v1.schema.json`, `toolset.v1.schema.json`)."""
    return json.loads(files("a4w_tools").joinpath("schemas", name).read_text(encoding="utf-8"))


def _errors(validator: Draft202012Validator, value: Any) -> list[str]:
    out = []
    for err in sorted(validator.iter_errors(value), key=lambda e: list(e.absolute_path)):
        where = "/".join(str(p) for p in err.absolute_path) or "(root)"
        out.append(f"{where}: {err.message}")
    return out


def validate_tool(definition: Any) -> list[str]:
    return _errors(_validator("tool.v1.schema.json"), definition)


def validate_toolset(manifest: Any) -> list[str]:
    return _errors(_validator("toolset.v1.schema.json"), manifest)


def check_references(tools: Mapping[str, Mapping[str, Any]]) -> list[str]:
    """상위 tool의 step이 같은 묶음의 단순 tool만, 올바른 입력으로 참조하는지 본다 (FR-225)."""
    errors: list[str] = []
    for name, tool in sorted(tools.items()):
        if tool.get("kind") != "composite":
            continue
        inputs = set(tool.get("inputSchema", {}).get("properties", {}))
        seen: list[str] = []
        for step in tool.get("steps", []):
            sid, target_name = step.get("id"), step.get("tool")
            where = f"{name}.steps[{sid}]"
            target = tools.get(target_name)
            if target is None:
                errors.append(f"{where}: 묶음에 없는 tool {target_name}")
            elif target.get("kind") != "operation":
                errors.append(f"{where}: 단순 tool만 참조할 수 있다 ({target_name})")
            else:
                props = set(target.get("inputSchema", {}).get("properties", {}))
                required = set(target.get("inputSchema", {}).get("required", []))
                args = set(step.get("args", {}))
                for extra in sorted(args - props):
                    errors.append(f"{where}: {target_name}의 입력이 아닌 인자 {extra}")
                for missing in sorted(required - args):
                    errors.append(f"{where}: {target_name}의 필수 입력 {missing}이 빠졌다")
            for arg, value in sorted(step.get("args", {}).items()):
                errors.extend(_check_value(f"{where}.{arg}", value, inputs, seen))
            seen.append(sid)
        output = tool.get("output")
        if output is not None and output.get("$step") not in seen:
            errors.append(f"{name}.output: 없는 step {output.get('$step')}")
    return errors


def _check_value(where: str, value: Mapping[str, Any], inputs: set[str], seen: list[str]) -> list[str]:
    if "$input" in value and value["$input"] not in inputs:
        return [f"{where}: 상위 tool 입력에 없는 {value['$input']}"]
    if "$step" in value and value["$step"] not in seen:
        return [f"{where}: 앞선 step이 아닌 {value['$step']}"]
    return []
