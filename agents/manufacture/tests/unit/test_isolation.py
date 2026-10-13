"""manufacture는 타겟과 단절한다 (Phase 2 설계 1절, AC-14).

타겟을 부르는 코드(실행기·HTTP 클라이언트·시험 환경·타겟 어댑터의 scope·오류 해석)를 import하지 않고,
타겟 주소·token 설정을 읽지 않는다.
"""

from __future__ import annotations

import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "a4w_manufacture"
FORBIDDEN_IMPORTS = [
    r"a4w_tools\.executor", r"a4w_tools\.target_http", r"a4w_tools\.sandbox", r"a4w_tools\.composite",
    r"a4w_target_workfluence\.(scopes|sandbox|errors)", r"a4w_detection",
]
FORBIDDEN_SETTINGS = ["TARGET_API_BASE_URL", "TARGET_API_TOKEN", "TLS_CA_FILE", "CALL_READ_PER_MINUTE"]


def sources():
    return {p: p.read_text(encoding="utf-8") for p in SRC.rglob("*.py")}


def test_no_target_calling_imports():
    bad = [(p.name, pat) for p, text in sources().items() for pat in FORBIDDEN_IMPORTS
           if re.search(rf"^\s*(from|import)\s+{pat}", text, re.MULTILINE)]
    assert bad == []


def test_no_target_secret_settings():
    bad = [(p.name, s) for p, text in sources().items() for s in FORBIDDEN_SETTINGS if s in text]
    assert bad == []
