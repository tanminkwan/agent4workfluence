"""추가된 API 반영 시험: seed 본보기 + seed에 없는 동작 → 새 tool.
실제 명세의 동작(seed에 없다고 가정) + 명세에 없는 가상 동작(모델이 미리 알 수 없음)."""
import sys, os, json, re, copy, concurrent.futures as cf
sys.argv = ['x']; sys.path.insert(0, '/w'); import eval as e
from probe_upgrade import PREV as SEED   # 본보기 2개 (search_pages, add_page_comment)

MODEL = os.environ.get("PROBE_MODEL", "openai/gpt-oss-120b"); EFFORT = os.environ.get("PROBE_EFFORT") or None
EXISTING = {"search_pages", "add_page_comment", "get_page", "update_page", "create_page", "list_page_tree"}

def synthetic():
    # 가상 동작 1: 페이지 구독 (경로 + body)
    w = {"operationId": "pages.watch", "method": "POST", "path": "/pages/{id}/watchers", "summary": "페이지 지켜보기",
         "description": "이 페이지가 바뀌면 알림을 받는다. 이미 지켜보고 있으면 200으로 그대로 둔다. `scope`가 tree면 하위 페이지까지 지켜본다.",
         "scope": ["write"], "inputs": [
            {"field": "id", "in": "path", "required": True, "schema": {"type": "string", "format": "uuid"}, "specDescription": "페이지의 id"},
            {"field": "scope", "in": "body", "required": False, "schema": {"type": "string", "enum": ["page", "tree"], "default": "page"}, "specDescription": "page(이 페이지만, 기본)·tree(하위 페이지까지)"}]}
    # 가상 동작 2: 역링크 목록 (query + 페이지 넘김)
    b = {"operationId": "pages.backlinks", "method": "GET", "path": "/pages/{id}/backlinks", "summary": "이 페이지를 가리키는 페이지",
         "description": "본문에서 이 페이지로 링크를 건 페이지 목록. 읽을 권한이 없는 페이지는 빠진다. 결과가 많으면 `nextCursor`가 오고, 그 값을 `cursor`로 다시 보내면 다음 묶음이다.",
         "scope": ["read"], "inputs": [
            {"field": "id", "in": "path", "required": True, "schema": {"type": "string", "format": "uuid"}, "specDescription": "페이지의 id"},
            {"field": "cursor", "in": "query", "required": False, "schema": {"type": "string"}, "specDescription": "앞 응답의 nextCursor"},
            {"field": "limit", "in": "query", "required": False, "schema": {"type": "integer", "minimum": 1, "maximum": 100, "default": 30}, "specDescription": "한 번에 돌려줄 수"}]}
    return {"pages.watch": (w, {"desc_any": ["tree", "하위"]}), "pages.backlinks": (b, {"desc_any": ["cursor", "nextCursor", "다음"]})}

REAL = ["labels.detach", "notifications.read", "comments.delete", "pages.versions", "pages.version", "attachments.list", "labels.pages", "trash.pages"]

SYS = e.SYS_A + ("\n\n이번 일은 새로 추가된 동작의 tool을 만드는 것이다. 아래 본보기 tool들과 이름 짓기·설명 문체를 맞춘다. "
                 "이미 있는 tool 이름과 겹치지 않게 한다.")

def check(sk, r, rub):
    j = r.get("json")
    if not j: return ["FAIL " + str(r.get("error"))[:120]]
    iss = []
    if not re.fullmatch(r"[a-z][a-z0-9_]{2,63}", j["name"]): iss.append("name 형식")
    if j["name"] in EXISTING: iss.append("이름 충돌 " + j["name"])
    want = {f["field"]: f for f in sk["inputs"]}; got = {i["field"]: i for i in j["inputs"]}
    if set(got) - set(want): iss.append(f"지어낸 칸 {sorted(set(got)-set(want))}")
    if set(want) - set(got): iss.append(f"빠진 칸 {sorted(set(want)-set(got))}")
    for f, i in got.items():
        if f in want and i["hidden"] and want[f]["required"] and i["default"] in (None, ""): iss.append(f"필수 {f} 숨김")
        if f in want and want[f]["in"] == "path" and i["hidden"]: iss.append(f"경로 {f} 숨김")
    if rub and "desc_any" in rub and not any(w in j["description"] for w in rub["desc_any"]): iss.append("제약 누락 " + "/".join(rub["desc_any"]))
    if len(j["exampleQueries"]) < 3: iss.append("예시 부족")
    if any(re.search(r"[0-9a-f]{8}-[0-9a-f]{4}", q) for q in j["exampleQueries"]): iss.append("예시에 UUID(부자연)")
    return iss

def run(item):
    op, sk, rub = item
    user = ("본보기 tool(이미 있음):\n" + json.dumps(list(SEED.values()), ensure_ascii=False, indent=1) +
            "\n\n이미 있는 tool 이름: " + ", ".join(sorted(EXISTING)) +
            "\n\n새로 추가된 동작:\n" + json.dumps(sk, ensure_ascii=False, indent=1))
    r = e.chat(MODEL, SYS, user, e.SCHEMA_A, "t", EFFORT, 12000)
    return op, (r.get("json") or {}).get("name"), check(sk, r, rub), r.get("sec")

items = [(op, e.skeleton(op), e.RUBRIC.get(op)) for op in REAL] + [(op, sk, rub) for op, (sk, rub) in synthetic().items()]
items = items * int(os.environ.get("PROBE_RUNS", "2"))
res = list(cf.ThreadPoolExecutor(6).map(run, items))
for x in res: print(x, flush=True)
ok = sum(not x[2] for x in res); print(f"SUMMARY {MODEL} effort={EFFORT}: {ok}/{len(res)} 통과, 평균 {sum(x[3] or 0 for x in res)/len(res):.1f}s")
