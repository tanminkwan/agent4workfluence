"""seed 도구 (Phase 2 설계 7.3절). 반영 흐름에서는 쓰지 않는 개발용 명령이다.

사람(개발 세션)이 쓴 글(seed/authoring.yaml)과 같은 기계 변환 골격으로 seed/tools/*.json을 만들고 지문을 넣는다.
상위 tool(seed/composites/*.json)은 위험 등급·scope·revision을 코드 규칙으로 맞춘다. 시나리오와 질의가 실제 tool을
가리키는지 확인한다.

  a4w-seedtool build --catalog DIR --spec openapi.yaml --limits description=600,summary=120,example=60 \
                     --max-visible 4 --max-chars 8000 --content-types application/json,multipart/form-data
  a4w-seedtool check ... (다시 만든 결과가 저장된 파일과 같은지만 본다. 다르면 종료 코드 1)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

import yaml

from a4w_core.openapi.operations import collect_operations
from a4w_tools.validate import check_references, validate_tool

from .assembly import assemble, parse_authoring, revision_of
from .checks import DefinitionChecker, TextLimits
from .fingerprint import fragment_sha, structure_sha
from .selection import RISK_ORDER, Selector
from .transform.openapi3 import OpenApi3Transformer


class SeedError(Exception):
    pass


def build_seeds(spec: Path, catalog: Path, *, limits: str, max_visible: int, max_chars: int,
                content_types: list[str]) -> dict[str, Any]:
    raw_spec = Path(spec).read_bytes()
    spec_version = hashlib.sha256(raw_spec).hexdigest()
    ops = collect_operations(yaml.safe_load(raw_spec))
    rules = yaml.safe_load((catalog / "rules.yaml").read_text(encoding="utf-8"))
    all_scopes = {s for f in ops.values() for req in f.get("security") or [] for v in req.values() for s in v}
    selector = Selector(rules, enabled_groups=list(rules["groups"]), token_scopes=all_scopes)
    transformer = OpenApi3Transformer(content_types=content_types)
    checker = DefinitionChecker(TextLimits.parse(limits), max_visible_inputs=max_visible)
    authoring = yaml.safe_load((catalog / "seed" / "authoring.yaml").read_text(encoding="utf-8")) or {}

    errors: list[str] = []
    tools: dict[str, dict[str, Any]] = {}
    tool_ops = set()
    for (method, path), frag in sorted(ops.items(), key=lambda kv: kv[1].get("operationId") or ""):
        sel = selector.select(frag)
        if sel.kind != "tool":
            continue
        op_id = frag["operationId"]
        tool_ops.add(op_id)
        if op_id not in authoring:
            errors.append(f"{op_id}: authoring.yaml에 글이 없다")
            continue
        sk = transformer.skeleton(frag, fixed=selector.fixed_values(frag))
        raw = json.loads(json.dumps(authoring[op_id]))
        raw.setdefault("inputs", {})
        for inp in sk.inputs:
            raw["inputs"].setdefault(inp.name, {"description": inp.description})
        problems = checker.check_authoring(sk, raw, taken_names=set(tools))
        if problems:
            errors.extend(f"{op_id}: {p.code} {p.field} {p.message}" for p in problems)
            continue
        source = {"operationId": op_id, "method": method, "path": path, "specVersion": spec_version,
                  "fragmentSha256": fragment_sha(frag), "structureSha256": structure_sha(frag)}
        d = assemble(sk, parse_authoring(raw, max_chars=max_chars), group=sel.group, risk=sel.risk, origin="seed",
                     source=source)
        tools[d["name"]] = d
    for op_id in sorted(set(authoring) - tool_ops):
        errors.append(f"{op_id}: tool 대상이 아닌 동작의 글이다 (명세·규칙을 확인)")

    composites = {}
    for f in sorted((catalog / "seed" / "composites").glob("*.json")):
        c = json.loads(f.read_text(encoding="utf-8"))
        steps = [s["tool"] for s in c["steps"]]
        missing = [s for s in steps if s not in tools]
        if missing:
            errors.append(f"{c['name']}: 없는 tool을 참조한다: {', '.join(missing)}")
            continue
        c["risk"] = max((tools[s]["risk"] for s in steps), key=RISK_ORDER.__getitem__)
        c["scopes"] = sorted({sc for s in steps for sc in tools[s]["scopes"]})
        c["origin"] = "seed"
        c["revision"] = revision_of(c)
        problems = validate_tool(c) + check_references({**tools, c["name"]: c})
        errors.extend(f"{c['name']}: {p}" for p in problems)
        composites[c["name"]] = c

    names = set(tools) | set(composites)
    for p in sorted((catalog / "scenarios").glob("*.yaml")):
        if p.stem not in names:
            errors.append(f"scenarios/{p.name}: 없는 tool의 시나리오다")
    queries = (yaml.safe_load((catalog / "queries.yaml").read_text(encoding="utf-8")) or {}).get("queries", [])
    for q in queries:
        bad = [n for n in q["expect"] if n not in names]
        if bad:
            errors.append(f"queries.yaml '{q['query']}': 없는 tool {', '.join(bad)}")
    if errors:
        raise SeedError("\n".join(errors))

    out_dir = catalog / "seed" / "tools"
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("*.json"):
        if old.stem not in tools:
            old.unlink()
    for name, d in tools.items():
        _write(out_dir / f"{name}.json", d)
    for name, c in composites.items():
        _write(catalog / "seed" / "composites" / f"{name}.json", c)
    return {"specVersion": spec_version, "tools": len(tools), "composites": len(composites), "queries": len(queries)}


def _write(path: Path, value: Any) -> None:
    # 키 순서를 지킨다 (입력 칸 순서가 모델에게 보이는 순서다). revision은 정규 JSON으로 따로 계산한다
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="a4w-seedtool")
    ap.add_argument("command", choices=["build", "check"])
    ap.add_argument("--catalog", required=True, type=Path)
    ap.add_argument("--spec", required=True, type=Path)
    ap.add_argument("--limits", required=True)
    ap.add_argument("--max-visible", required=True, type=int)
    ap.add_argument("--max-chars", required=True, type=int)
    ap.add_argument("--content-types", required=True)
    a = ap.parse_args(argv)
    kw = dict(limits=a.limits, max_visible=a.max_visible, max_chars=a.max_chars,
              content_types=[c.strip() for c in a.content_types.split(",") if c.strip()])
    try:
        if a.command == "build":
            print(json.dumps(build_seeds(a.spec, a.catalog, **kw), ensure_ascii=False))
            return 0
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / "catalog"
            shutil.copytree(a.catalog, copy)
            build_seeds(a.spec, copy, **kw)
            stale = [str(p.relative_to(copy)) for p in sorted(copy.rglob("*.json"))
                     if not (a.catalog / p.relative_to(copy)).is_file()
                     or (a.catalog / p.relative_to(copy)).read_bytes() != p.read_bytes()]
            stale += [str(p.relative_to(a.catalog)) for p in sorted((a.catalog / "seed").rglob("*.json"))
                      if not (copy / p.relative_to(a.catalog)).is_file()]
        if stale:
            print("seed가 명세·글과 다르다 (a4w-seedtool build로 다시 만든다):\n  " + "\n  ".join(stale), file=sys.stderr)
            return 1
        return 0
    except SeedError as e:
        print(str(e), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
