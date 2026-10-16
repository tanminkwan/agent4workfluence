#!/usr/bin/env bash
# 타겟 변화 받아들이기: 변경 식별 → 반영 → 결과 확인 → (승인된 보류가 있으면) 정리 반영.
# cron·systemd timer·workflow 도구가 주기적으로 부른다. 몇 번을 다시 불러도 안전하다(멱등).
# 종료 코드: 0 끝남(변화 없음 포함), 2 사람 검토를 기다리는 보류가 있음, 1 실패(다음 실행에서 다시 한다)
set -euo pipefail
TARGET="${A4W_TARGET:?A4W_TARGET (예: workfluence)}"
DET="${A4W_DETECTION_URL:?A4W_DETECTION_URL (예: http://localhost:18001)}"
MAN="${A4W_MANUFACTURE_URL:?A4W_MANUFACTURE_URL (예: http://localhost:18002)}"
POLL="${A4W_POLL_SECONDS:-5}"
TIMEOUT="${A4W_APPLY_TIMEOUT_SECONDS:-1800}"
STATE_DIR="${A4W_STATE_DIR:-/var/lib/a4w-sync}"     # 받은 changeSetId 기록 (빠짐없이, 순서대로 반영하기 위해)

call() {  # call <token> <curl 인자...>: BODY·CODE를 채운다. token이 비면 Authorization을 붙이지 않는다
  local tok="$1"; shift
  local -a h=(); [ -n "$tok" ] && h=(-H "Authorization: Bearer $tok")
  local out; out="$(curl -sS --max-time 120 -w '\n%{http_code}' "${h[@]}" -H 'Content-Type: application/json' "$@")"
  BODY="$(sed '$d' <<<"$out")"; CODE="$(tail -n1 <<<"$out")"
}
det() { call "${A4W_DETECTION_TOKEN:-}" "$@"; }
man() { call "${A4W_MANUFACTURE_TOKEN:-}" "$@"; }
log() { echo "$(date -u +%FT%TZ) a4w-sync target=$TARGET $*" >&2; }

apply_and_wait() {  # apply_and_wait <요청 본문 JSON>: 끝난 반영 결과를 BODY에 남긴다
  man -X POST "$MAN/v1/apply" -d "$1"
  case "$CODE" in
    200) return 0 ;;                                            # 이미 반영한 변경분: 앞선 결과
    202) ;;
    409) log "apply conflict $(jq -c .error <<<"$BODY")"; return 1 ;;   # APPLY_IN_PROGRESS·CHANGESET_OUT_OF_ORDER
    *)   log "apply http=$CODE $BODY"; return 1 ;;
  esac
  local id; id="$(jq -r .applyId <<<"$BODY")"; local end=$(( $(date +%s) + TIMEOUT ))
  while :; do
    man "$MAN/v1/applies/$id"
    [ "$CODE" = 200 ] || { log "apply $id lookup http=$CODE"; return 1; }
    [ "$(jq -r .status <<<"$BODY")" != running ] && return 0
    [ "$(date +%s)" -ge "$end" ] && { log "apply $id still running after ${TIMEOUT}s"; return 1; }
    sleep "$POLL"
  done
}
report() { log "$1 apply=$(jq -r .applyId <<<"$BODY") status=$(jq -r .status <<<"$BODY") toolVersion=$(jq -r .toolVersion <<<"$BODY") summary=$(jq -c .summary <<<"$BODY")"; }

mkdir -p "$STATE_DIR"; QUEUE="$STATE_DIR/$TARGET.changesets"; touch "$QUEUE"

# 1. 변경 식별 (동기). 명세 해시가 같으면 명세를 받지 않아 가볍다
det -X POST "$DET/v1/detect" -d "{\"targetId\":\"$TARGET\"}"
case "$CODE" in
  200) ;;
  409) log "detect already running (skip)"; exit 0 ;;
  *)   log "detect http=$CODE $BODY"; exit 1 ;;
esac
if [ "$(jq -r .status <<<"$BODY")" = changed ]; then
  CS="$(jq -r .changeSetId <<<"$BODY")"
  grep -qx "$CS" "$QUEUE" || echo "$CS" >> "$QUEUE"
  log "changed changeSet=$CS needsReview=$(jq -r .needsReview <<<"$BODY") summary=$(jq -c .summary <<<"$BODY")"
fi

# 2. 기록한 변경분을 받은 순서대로 반영한다
while read -r CS; do
  [ -n "$CS" ] || continue
  apply_and_wait "{\"targetId\":\"$TARGET\",\"changeSetId\":\"$CS\"}" || exit 1
  report "changeSet=$CS"
  [ "$(jq -r .status <<<"$BODY")" = failed ] && { log "apply error $(jq -c .error <<<"$BODY")"; exit 1; }  # 바뀐 것 없음, 다음에 다시
  sed -i "\|^$CS\$|d" "$QUEUE"
done < <(cat "$QUEUE")

# 3. 사람이 승인한 보류가 있으면 정리 반영으로 서빙에 넣는다
man "$MAN/v1/reviews?targetId=$TARGET&status=approved"
if [ "$CODE" = 200 ] && [ "$(jq '.reviews | length' <<<"$BODY")" -gt 0 ]; then
  apply_and_wait "{\"targetId\":\"$TARGET\"}" || exit 1
  report "cleanup"
fi

# 4. 아직 사람 결정을 기다리는 보류
man "$MAN/v1/reviews?targetId=$TARGET&status=pending"
[ "$CODE" = 200 ] || { log "reviews http=$CODE"; exit 1; }
N="$(jq '.reviews | length' <<<"$BODY")"
if [ "$N" -gt 0 ]; then
  log "pending reviews=$N"
  jq -c '.reviews[] | {reviewId, tool, reasons}' <<<"$BODY" >&2
  exit 2
fi
exit 0
