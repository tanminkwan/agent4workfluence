"""변경 반영 시험: 이전 정의(seed 흉내) + 바뀐 명세 조각 → 이름 유지, 바뀐 부분 반영."""
import sys, json, copy, concurrent.futures as cf
sys.argv = ['x']; sys.path.insert(0, '/w'); import eval as e

PREV = {
 "search.find": {"name": "search_pages", "description": "제목과 본문에서 검색어로 페이지를 찾는다. 검색어는 두 글자 이상이어야 한다. space로 범위를 좁힐 수 있고 limit은 최대 50이다.",
   "inputs": [{"field": "q", "description": "찾을 검색어(두 글자 이상).", "hidden": False, "default": None},
              {"field": "space", "description": "검색 범위를 좁힐 스페이스 이름이나 id. spaces 목록에서 얻는다.", "hidden": False, "default": None},
              {"field": "limit", "description": "돌려받을 최대 개수(1~50).", "hidden": True, "default": "20"}],
   "indexSummary": "검색어로 위키 페이지 찾기(제목·본문)", "exampleQueries": ["배포 절차 문서 찾아줘", "VPN 설정 방법 검색해줘", "운영팀 스페이스에서 장애 보고서 찾아줘"]},
 "comments.create": {"name": "add_page_comment", "description": "페이지에 댓글을 단다. 본문은 마크다운이다.",
   "inputs": [{"field": "pageId", "description": "댓글을 달 페이지 id. 검색이나 페이지 목록에서 얻는다.", "hidden": False, "default": None},
              {"field": "body", "description": "댓글 내용(마크다운).", "hidden": False, "default": None},
              {"field": "format", "description": "본문 형식.", "hidden": True, "default": "markdown"}],
   "indexSummary": "위키 페이지에 댓글 달기", "exampleQueries": ["이 문서에 확인했다고 댓글 남겨줘", "회의록에 질문 댓글 달아줘", "페이지에 의견 남겨줘"]},
}

def mutate(op):
    sk = e.skeleton(op)
    if op == "search.find":   # 새 query 칸 추가
        sk["inputs"].append({"field": "label", "in": "query", "required": False, "schema": {"type": "string", "maxLength": 100},
                             "specDescription": "이 라벨이 붙은 페이지만 찾는다(라벨 이름)"})
        change = "added query parameter `label`"
    else:                     # 필수 칸 추가
        sk["inputs"].append({"field": "parentCommentId", "in": "body", "required": False, "schema": {"type": "string", "format": "uuid"},
                             "specDescription": "답글이면 부모 댓글의 id — 안 주면 새 댓글"})
        change = "added body field `parentCommentId`"
    return sk, change

SYS = e.SYS_A + "\n\n이번 일은 변경 반영이다. 이전 tool 정의가 주어진다. name은 절대 바꾸지 않는다. 바뀐 명세에 맞게 필요한 부분만 고치고, 바뀌지 않은 칸의 설명은 그대로 둔다."

def run(op):
    sk, change = mutate(op)
    user = ("이전 tool 정의:\n" + json.dumps(PREV[op], ensure_ascii=False, indent=1) +
            f"\n\n바뀐 점: {change}\n\n바뀐 뒤 동작 명세:\n" + json.dumps(sk, ensure_ascii=False, indent=1))
    import os; r = e.chat(os.environ.get("PROBE_MODEL", "openai/gpt-oss-120b"), SYS, user, e.SCHEMA_A, "t", os.environ.get("PROBE_EFFORT") or None, 12000)
    j = r.get("json")
    if not j: return (op, "FAIL", r.get("error"), r.get("finish"))
    new_field = sk["inputs"][-1]["field"]
    prev_desc = {i["field"]: i["description"] for i in PREV[op]["inputs"]}
    got = {i["field"]: i for i in j["inputs"]}
    kept = sum(got.get(f, {}).get("description") == d for f, d in prev_desc.items())
    sc = e.score_a(op, r)
    return (op, "name_kept" if j["name"] == PREV[op]["name"] else f"NAME_CHANGED->{j['name']}",
            f"new_field_present={new_field in got}", f"new_field_hidden={got.get(new_field,{}).get('hidden')}",
            f"mentions_in_desc={new_field in j['description']}", f"unchanged_desc_kept={kept}/{len(prev_desc)}",
            sc["issues"], r.get("sec"))

if __name__ == "__main__":
    with cf.ThreadPoolExecutor(6) as ex:
        for res in ex.map(run, [op for op in PREV for _ in range(3)]): print(res, flush=True)
