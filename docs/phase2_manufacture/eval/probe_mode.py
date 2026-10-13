# 응답 형식 모드(json_schema / json_object)와 추론 강도에 따른 출력 반복 확인
import sys, json, concurrent.futures as cf
sys.argv=['x']; sys.path.insert(0,'/w'); import eval as e
import urllib.request, time
def call(op, effort, mode, temp):
    sk=e.skeleton(op)
    body={"model":__import__("os").environ.get("PROBE_MODEL","openai/gpt-oss-120b"),"messages":[{"role":"system","content":e.SYS_A},{"role":"user","content":"다음 동작의 tool을 설계하라.\n"+json.dumps(sk,ensure_ascii=False,indent=1)}],"max_tokens":12000,"temperature":temp}
    if effort: body["reasoning_effort"]=effort
    if mode=="schema": body["response_format"]={"type":"json_schema","json_schema":{"name":"t","strict":True,"schema":e.SCHEMA_A}}
    elif mode=="object": body["response_format"]={"type":"json_object"}; body["messages"][0]["content"]+="\n출력은 다음 JSON Schema를 따르는 JSON 하나만:\n"+json.dumps(e.SCHEMA_A)
    req=urllib.request.Request(e.BASE+"/v1/chat/completions",data=json.dumps(body).encode(),headers={"Authorization":"Bearer "+e.KEY,"Content-Type":"application/json"})
    t=time.time(); d=json.load(urllib.request.urlopen(req,timeout=600)); c=d["choices"][0]
    ok=False
    try: j=json.loads(c["message"]["content"]); ok=isinstance(j,dict) and "inputs" in j; sc=e.score_a(op,{"json":j}) if ok else None
    except Exception: sc=None
    return (op,effort,mode,temp,c["finish_reason"],d["usage"]["completion_tokens"],round(time.time()-t),ok, sc and sc["issues"])
jobs=[(op,ef,mo,te) for op in ["search.find","pages.tree"] for (ef,mo,te) in [("high","schema",0.2),("medium","schema",0.2),("low","schema",0.2),("high","schema",0.0),("high","object",0.2),("medium","object",0.2)] for _ in range(2)]
with cf.ThreadPoolExecutor(6) as ex:
    for r in ex.map(lambda a: call(*a), jobs): print(r, flush=True)
