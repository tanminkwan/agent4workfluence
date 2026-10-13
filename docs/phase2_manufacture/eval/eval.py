"""일회성 평가: LLM이 Manufacture의 tool 제작 작업을 할 수 있는 수준인지 본다.

A. 단일 tool 작성 (이름·설명·입력 설명·숨김/기본값·색인 요약·예시 질의)  — 자동 채점
B. 상위 tool(묶음) 제안 — 참조 무결성 자동 채점
C. 만든 설명으로 작은 모델이 한국어 요청에 맞는 tool을 고르는지 — 원 명세 summary 기준선과 비교
"""
import json, os, re, sys, time, urllib.request, urllib.error, concurrent.futures as cf
import yaml
from a4w_core.openapi.operations import collect_operations

BASE = os.environ["LLM_BASE_URL"].rstrip("/")
KEY = os.environ["LLM_API_KEY"]
OUT = os.environ.get("EVAL_OUT", "/w/out")
os.makedirs(OUT, exist_ok=True)

doc = yaml.safe_load(open("/w/openapi.yaml"))
OPS = {o["operationId"]: {**o, "method": m, "path": pth} for (m, pth), o in collect_operations(doc).items()}

ADMIN_EXCL = {k for k, o in OPS.items() if any("admin" in s.get("bearerAuth", []) for s in (o.get("security") or []))}
OUT_OF_SCOPE = ADMIN_EXCL | {k for k in OPS if k.startswith(("chat.", "spec."))} | {
    "spaces.create", "spaces.delete", "spaces.update", "spaces.setStatus", "spaces.addMember", "spaces.removeMember",
    "spaces.setMemberRole", "categories.create", "categories.delete", "categories.rename", "templates.create",
    "templates.delete", "templates.update", "pages.export", "trash.restoreSpace", "trash.spaces"}
IN_SCOPE = sorted(k for k in OPS if k not in OUT_OF_SCOPE)

TASK_A_OPS = ["search.find", "pages.get", "pages.update", "pages.create", "pages.tree", "comments.create",
              "comments.update", "labels.attach", "notifications.readAll", "attachments.upload",
              "attachments.download", "pages.diff"]


def skeleton(op_id):
    """기계 변환 흉내: 입력 칸과 위치."""
    o = OPS[op_id]
    fields = []
    for p in o.get("parameters") or []:
        fields.append({"field": p["name"], "in": p["in"], "required": bool(p.get("required")),
                       "schema": p.get("schema"), "specDescription": p.get("description")})
    rb = o.get("requestBody")
    if rb:
        js = rb["content"].get("application/json", {}).get("schema", {})
        req = set(js.get("required") or [])
        for name, sch in (js.get("properties") or {}).items():
            sch = dict(sch); d = sch.pop("description", None)
            fields.append({"field": name, "in": "body", "required": name in req, "schema": sch, "specDescription": d})
    return {"operationId": op_id, "method": o["method"].upper(),
            "path": o.get("path"), "summary": o.get("summary"), "description": o.get("description"),
            "scope": [s for sec in (o.get("security") or []) for s in sec.get("bearerAuth", [])], "inputs": fields}


def resp_paths(op_id):
    """응답 JSON의 필드 경로 집합 (배열은 [])."""
    o = OPS[op_id]; paths = set()
    for code, r in (o.get("responses") or {}).items():
        if not str(code).startswith("2"):
            continue
        sch = (r.get("content") or {}).get("application/json", {}).get("schema")
        def walk(s, pre, depth):
            if not isinstance(s, dict) or depth > 6:
                return
            for alt in (s.get("anyOf") or []) + (s.get("oneOf") or []) + (s.get("allOf") or []):
                walk(alt, pre, depth + 1)
            if s.get("type") == "array" or "items" in s:
                walk(s.get("items"), pre + "[]", depth + 1)
            for k, v in (s.get("properties") or {}).items():
                p = f"{pre}.{k}" if pre else k
                paths.add(p); walk(v, p, depth + 1)
        walk(sch, "", 0)
    return paths


def chat(model, system, user, schema, name, effort=None, max_tokens=16000, timeout=600):
    system = system + "\n\n출력은 다음 JSON Schema를 따르는 JSON 객체 하나만 쓴다:\n" + json.dumps(schema, ensure_ascii=False)
    body = {"model": model, "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "response_format": {"type": "json_object"},
            "max_tokens": max_tokens, "temperature": 0.2}
    if effort:
        body["reasoning_effort"] = effort
    req = urllib.request.Request(BASE + "/v1/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Authorization": "Bearer " + KEY, "Content-Type": "application/json"})
    t = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.load(r)
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code} {e.read()[:300]!r}", "sec": time.time() - t}
    except Exception as e:
        return {"error": repr(e), "sec": time.time() - t}
    c = d["choices"][0]
    res = {"sec": round(time.time() - t, 1), "finish": c.get("finish_reason"), "usage": d.get("usage")}
    try:
        res["json"] = json.loads(c["message"]["content"])
        miss = [k for k in schema.get("required", []) if k not in res["json"]]
        if miss:
            res["error"] = f"schema: missing {miss}"; res.pop("json")
    except Exception as e:
        res["error"] = f"parse {e!r}: {str(c['message'].get('content'))[:200]}"
    return res


SYS_A = """너는 API 명세를 보고, 작은 LLM agent(gpt-oss급)가 쓰기 좋은 tool을 설계하는 전문가다.
원칙:
- 한 tool은 한 가지 일을 한다. 이름은 영어 snake_case 동사_대상 형태(예: add_page_comment), 64자 이하.
- description은 한국어로 2~4문장. 언제 쓰는지, 꼭 알아야 할 제약(버전 충돌, 형식, 앞서 읽어야 할 값 등)을 명세에서 찾아 적는다. 명세에 없는 사실을 지어내지 않는다.
- inputs에는 주어진 입력 칸만 하나도 빠짐없이 적는다. 새 칸을 만들지 않는다.
- 작은 모델이 헷갈릴 칸은 hidden=true로 숨기고 default에 고정값을 준다. 필수 칸은 default를 줄 때만 숨길 수 있다. 숨기지 않으면 default는 null.
- 각 칸 description은 한국어 한 문장으로, 값을 어디서 얻는지 알려 준다.
- indexSummary는 자연어 검색용 한국어 요약(200자 이하).
- exampleQueries는 사람이 이 tool을 쓰게 될 한국어 요청 예시 3개."""

SCHEMA_A = {"type": "object", "additionalProperties": False,
            "required": ["name", "description", "inputs", "indexSummary", "exampleQueries"],
            "properties": {
                "name": {"type": "string"}, "description": {"type": "string"},
                "inputs": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                                                       "required": ["field", "description", "hidden", "default"],
                                                       "properties": {"field": {"type": "string"},
                                                                      "description": {"type": "string"},
                                                                      "hidden": {"type": "boolean"},
                                                                      "default": {"type": ["string", "null"]}}}},
                "indexSummary": {"type": "string"},
                "exampleQueries": {"type": "array", "items": {"type": "string"}}}}

# 동작별 꼭 다뤄야 할 것 (명세에 있는 제약)
RUBRIC = {
    "pages.update": {"desc_any": ["baseVersionNo", "currentVersionNo", "기준 버전"], "desc_any2": ["409", "충돌"]},
    "attachments.upload": {"desc_any": ["base64"]},
    "attachments.download": {"want_default": ("format", "json")},
    "search.find": {"desc_any": ["두 글자", "2글자", "2자"]},
    "pages.create": {"desc_any": ["space", "스페이스"]},
}


def score_a(op_id, r):
    sk = skeleton(op_id); issues = []
    j = r.get("json")
    if not j:
        return {"ok": False, "issues": [r.get("error") or "no json"]}
    if not re.fullmatch(r"[a-z][a-z0-9_]{2,63}", j["name"]):
        issues.append("name 형식")
    want = {f["field"]: f for f in sk["inputs"]}
    got = {i["field"]: i for i in j["inputs"]}
    if set(got) - set(want):
        issues.append(f"지어낸 칸 {sorted(set(got) - set(want))}")
    if set(want) - set(got):
        issues.append(f"빠진 칸 {sorted(set(want) - set(got))}")
    for f, i in got.items():
        if f in want and i["hidden"] and want[f]["required"] and i["default"] in (None, ""):
            issues.append(f"필수 칸 {f}를 기본값 없이 숨김")
        if f in want and want[f]["in"] == "path" and i["hidden"]:
            issues.append(f"경로 칸 {f} 숨김")
    rub = RUBRIC.get(op_id, {})
    for k in ("desc_any", "desc_any2"):
        if k in rub and not any(w in j["description"] for w in rub[k]):
            issues.append(f"설명에 제약 누락({'/'.join(rub[k])})")
    if "want_default" in rub:
        f, v = rub["want_default"]
        if not (f in got and got[f]["default"] == v):
            issues.append(f"{f}={v} 기본값 아님")
    if len(j["exampleQueries"]) < 3:
        issues.append("예시 질의 부족")
    return {"ok": not issues, "issues": issues, "name": j["name"]}


def task_a(model, effort):
    def one(op):
        sk = skeleton(op)
        return op, chat(model, SYS_A, "다음 동작의 tool을 설계하라.\n" + json.dumps(sk, ensure_ascii=False, indent=1),
                        SCHEMA_A, "tool_design", effort)
    with cf.ThreadPoolExecutor(4) as ex:
        return dict(ex.map(one, TASK_A_OPS))


SYS_B = """너는 작은 LLM agent가 쓰기 좋은 tool 묶음을 설계하는 전문가다. 아래 동작 목록만 써서,
사람이 자주 하는 일을 한 번에 끝내는 상위 tool을 3~5개 제안하라. 상위 tool은 동작을 순서대로 부르는 step 목록이다.
- step의 operationId는 목록에 있는 것만 쓴다.
- bindings는 그 step 동작의 입력 칸(field)에 넣을 값의 출처(source)다. source 문법:
  input.<상위 tool 입력 이름> | steps[<앞 step 번호>].<응답 필드 경로, 배열은 [0]> | const:<값>
- 각 step의 필수 입력 칸은 모두 bindings에 있어야 한다. 응답 필드 경로는 주어진 응답 필드만 쓴다.
- inputs는 상위 tool의 입력 이름 목록이다. 이름·설명은 위 원칙(영어 snake_case 이름, 한국어 설명)을 따른다."""

SCHEMA_B = {"type": "object", "additionalProperties": False, "required": ["composites"], "properties": {
    "composites": {"type": "array", "items": {"type": "object", "additionalProperties": False,
        "required": ["name", "description", "inputs", "steps"], "properties": {
            "name": {"type": "string"}, "description": {"type": "string"},
            "inputs": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                       "required": ["name", "description"], "properties": {"name": {"type": "string"}, "description": {"type": "string"}}}},
            "steps": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                      "required": ["operationId", "bindings"], "properties": {
                          "operationId": {"type": "string"},
                          "bindings": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                                       "required": ["field", "source"], "properties": {"field": {"type": "string"}, "source": {"type": "string"}}}}}}}}}}}}


def catalog():
    out = []
    for k in IN_SCOPE:
        sk = skeleton(k)
        out.append({"operationId": k, "summary": sk["summary"],
                    "inputs": [{"field": f["field"], "required": f["required"]} for f in sk["inputs"]],
                    "responseFields": sorted(resp_paths(k))[:40]})
    return out


def score_b(r):
    j = r.get("json")
    if not j:
        return {"ok": False, "issues": [r.get("error")], "n": 0}
    issues = []; n = len(j["composites"])
    for c in j["composites"]:
        ins = {i["name"] for i in c["inputs"]}
        for si, st in enumerate(c["steps"]):
            op = st["operationId"]
            if op not in IN_SCOPE:
                issues.append(f"{c['name']}: 범위 밖/없는 동작 {op}"); continue
            sk = {f["field"]: f for f in skeleton(op)["inputs"]}
            bound = set()
            for b in st["bindings"]:
                if b["field"] not in sk:
                    issues.append(f"{c['name']} s{si}: 없는 칸 {op}.{b['field']}")
                bound.add(b["field"])
                src = b["source"]
                m = re.fullmatch(r"steps\[(\d+)\]\.(.+)", src)
                if src.startswith("input."):
                    if src[6:] not in ins:
                        issues.append(f"{c['name']} s{si}: 정의 안 된 입력 {src}")
                elif m:
                    k = int(m.group(1))
                    if k >= si:
                        issues.append(f"{c['name']} s{si}: 뒤 step 참조 {src}")
                    else:
                        path = re.sub(r"\[\d+\]", "[]", m.group(2))
                        if path not in resp_paths(c["steps"][k]["operationId"]):
                            issues.append(f"{c['name']} s{si}: 없는 응답 경로 {src}")
                elif not src.startswith("const:"):
                    issues.append(f"{c['name']} s{si}: source 문법 {src}")
            miss = [f for f, v in sk.items() if v["required"] and f not in bound]
            if miss:
                issues.append(f"{c['name']} s{si}: 필수 칸 누락 {op}.{miss}")
    return {"ok": not issues, "issues": issues, "n": n}


QUERIES = [  # (요청, 기대 operationId)
    ("'배포 절차'라는 말이 들어간 문서 찾아줘", "search.find"),
    ("운영팀 스페이스에 어떤 페이지들이 있는지 보여줘", "pages.tree"),
    ("이 페이지 내용 좀 읽어줘", "pages.get"),
    ("회의록 페이지 제목을 '10월 회의록'으로 바꿔줘", "pages.update"),
    ("개발 스페이스에 '신규 입사자 안내' 페이지 하나 만들어줘", "pages.create"),
    ("이 문서에 '확인했습니다' 라고 댓글 남겨줘", "comments.create"),
    ("내가 단 댓글 오타 고쳐줘", "comments.update"),
    ("이 페이지에 '긴급' 태그 달아줘", "labels.attach"),
    ("알림 전부 읽음으로 처리해줘", "notifications.readAll"),
    ("이 로그 파일을 페이지에 첨부해줘", "attachments.upload"),
    ("첨부된 설정 파일 내용 열어줘", "attachments.download"),
    ("3번 버전이랑 5번 버전 뭐가 달라졌는지 비교해줘", "pages.diff"),
    ("장애 대응 매뉴얼 본문에 연락처 추가해줘", "pages.update"),
    ("위키에서 VPN 설정 방법 검색해줘", "search.find"),
    ("이 페이지 밑에 하위 페이지로 '부록' 만들어줘", "pages.create"),
]

SCHEMA_C = {"type": "object", "additionalProperties": False, "required": ["choices"], "properties": {
    "choices": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                "required": ["index", "tool"], "properties": {"index": {"type": "integer"}, "tool": {"type": "string"}}}}}}


def task_c(tools, picker_model):
    """tools: [(name, description, operationId)] → 정확도."""
    lst = "\n".join(f"- {n}: {d}" for n, d, _ in tools)
    qs = "\n".join(f"{i}. {q}" for i, (q, _) in enumerate(QUERIES))
    r = chat(picker_model, "너는 사용자 요청에 맞는 tool 하나를 고르는 agent다. 목록의 이름만 쓴다.",
             f"tool 목록:\n{lst}\n\n요청마다 가장 알맞은 tool 이름을 골라라.\n{qs}", SCHEMA_C, "pick", None, 8000)
    if not r.get("json"):
        return {"acc": None, "error": r.get("error")}
    name2op = {n: op for n, _, op in tools}
    picked = {c["index"]: name2op.get(c["tool"]) for c in r["json"]["choices"]}
    wrong = [(q, exp, picked.get(i)) for i, (q, exp) in enumerate(QUERIES) if picked.get(i) != exp]
    return {"acc": round(1 - len(wrong) / len(QUERIES), 3), "wrong": wrong}


def main():
    models = sys.argv[1].split(",")
    effort = sys.argv[2] if len(sys.argv) > 2 and sys.argv[2] != "none" else None
    runs = int(sys.argv[3]) if len(sys.argv) > 3 else 1
    picker = os.environ.get("EVAL_PICKER_MODEL", "openai/gpt-oss-120b")
    report = {"effort": effort, "inScope": len(IN_SCOPE), "models": {}}
    # 기준선: 원 명세 summary/operationId로 고르기
    base_tools = [(op.replace(".", "_"), (OPS[op].get("summary") or ""), op) for op in TASK_A_OPS]
    report["baselineC"] = task_c(base_tools, picker)
    for m in models:
        mr = {"A": [], "B": [], "C": []}
        for run in range(runs):
            ra = task_a(m, effort)
            json.dump(ra, open(f"{OUT}/A_{m.replace('/', '_')}_{run}.json", "w"), ensure_ascii=False, indent=1)
            sa = {op: score_a(op, r) for op, r in ra.items()}
            secs = [r.get("sec", 0) for r in ra.values()]
            toks = [((r.get("usage") or {}).get("completion_tokens") or 0) for r in ra.values()]
            mr["A"].append({"pass": sum(s["ok"] for s in sa.values()), "of": len(sa),
                            "issues": {op: s["issues"] for op, s in sa.items() if s["issues"]},
                            "names": {op: s.get("name") for op, s in sa.items()},
                            "secAvg": round(sum(secs) / len(secs), 1), "secMax": max(secs),
                            "outTokAvg": round(sum(toks) / len(toks))})
            tools = [(r["json"]["name"], r["json"]["description"], op) for op, r in ra.items() if r.get("json")]
            mr["C"].append(task_c(tools, picker))
            rb = chat(m, SYS_B, "동작 목록:\n" + json.dumps(catalog(), ensure_ascii=False), SCHEMA_B, "composites", effort, 32000)
            json.dump(rb, open(f"{OUT}/B_{m.replace('/', '_')}_{run}.json", "w"), ensure_ascii=False, indent=1)
            sb = score_b(rb); sb["sec"] = rb.get("sec")
            sb["names"] = [c["name"] for c in (rb.get("json") or {}).get("composites", [])]
            mr["B"].append(sb)
        if runs > 1:
            names = [a["names"] for a in mr["A"]]
            mr["nameStability"] = round(sum(len({n[op] for n in names}) == 1 for op in TASK_A_OPS) / len(TASK_A_OPS), 2)
        report["models"][m] = mr
    print(json.dumps(report, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
