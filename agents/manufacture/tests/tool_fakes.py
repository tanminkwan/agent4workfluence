"""시험용 tool 정의."""

from __future__ import annotations

from a4w_manufacture.assembly import revision_of


def tool(name, op_id=None, risk="write", method="POST", path=None, scopes=("write",), group="communication"):
    t = {
        "schema": "a4w.tool/v1", "kind": "operation", "name": name, "title": name, "description": name,
        "inputSchema": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
        "http": {"method": method, "path": path or f"/{name}", "bindings": [], "fixed": [], "success": [200]},
        "response": {"maxChars": 1000}, "risk": risk, "scopes": list(scopes), "group": group,
        "index": {"summary": name, "examples": ["가", "나", "다"]},
        "source": {"operationId": op_id or name.replace("_", "."), "method": method, "path": path or f"/{name}",
                   "specVersion": "v", "fragmentSha256": "a" * 64, "structureSha256": "b" * 64},
        "origin": "seed",
    }
    t["revision"] = revision_of(t)
    return t
