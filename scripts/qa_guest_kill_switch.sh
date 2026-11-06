#!/usr/bin/env bash
# Local e2e QA for the guest kill switch (AIPLAT-1450).
#
# Needs the compose stack up: docker compose -f litellm_docker_compose.yaml up -d
# Starts its own MLPA on $QA_PORT with exported env vars (never touches .env),
# then checks both levers: the MLPA Redis flag and LiteLLM /key/block.
#
# Optional: FXA_TOKEN=<token> to also check a real FxA user keeps working.
set -uo pipefail
cd "$(dirname "$0")/.."

QA_PORT=${QA_PORT:-8091}
TTL=${TTL:-2}
MLPA="http://localhost:${QA_PORT}"
LITELLM="http://localhost:4000"
FLAG_KEY="mlpa:guests_enabled"
LOG=/tmp/qa_guest_kill_switch_mlpa.log
COMPOSE="docker compose -f litellm_docker_compose.yaml"
MASTER_KEY=${MASTER_KEY:-$(docker exec litellm printenv MASTER_KEY)}
FAILED=0

pass() { echo "PASS  $*"; }
fail() { echo "FAIL  $*"; FAILED=1; }
check() { # check <name> <expected> <actual>
    if [ "$2" = "$3" ]; then pass "$1 ($3)"; else fail "$1: expected $2, got $3"; fi
}
redis() { docker exec litellm_redis redis-cli "$@"; }
litellm_admin() { # litellm_admin <path> <json>
    curl -s -X POST "$LITELLM$1" -H "Authorization: Bearer $MASTER_KEY" \
        -H "Content-Type: application/json" -d "$2"
}
code() { curl -s -o /tmp/qa_body -w '%{http_code}' "$@"; }
body() { cat /tmp/qa_body; }
guest_token_code() { code -H "Accept: application/json" "$MLPA/verify/v1/guest"; }
guest_completion_code() {
    code -X POST "$MLPA/v1/chat/completions" -H "Authorization: Bearer $GUEST_TOKEN" \
        -H "service-type: ai" -H "Content-Type: application/json" \
        -d '{"model":"mock","messages":[{"role":"user","content":"hi"}]}'
}
other_key_code() { # non-guest LiteLLM key, stands in for FxA spend at LiteLLM
    code -X POST "$LITELLM/v1/chat/completions" -H "Authorization: Bearer $OTHER_KEY" \
        -H "Content-Type: application/json" \
        -d '{"model":"mock","messages":[{"role":"user","content":"hi"}]}'
}
fxa_code() {
    code -X POST "$MLPA/v1/chat/completions" -H "Authorization: Bearer $FXA_TOKEN" \
        -H "service-type: ai" -H "Content-Type: application/json" \
        -d '{"model":"mock","messages":[{"role":"user","content":"hi"}]}'
}
check_non_guest() {
    check "$1: non-guest LiteLLM key works" 200 "$(other_key_code)"
    if [ -n "${FXA_TOKEN:-}" ]; then
        check "$1: FxA user works" 200 "$(fxa_code)"
    fi
}
flip() { # flip <enable|disable> [master_key]
    code -X POST "$MLPA/user/guests/$1" -H "master_key: Bearer ${2:-$MASTER_KEY}"
}
wait_for() { # wait_for <code|!code> <fn> <timeout_s>, prints elapsed seconds
    local start now got hit
    start=$(python3 -c 'import time; print(time.time())')
    while :; do
        got=$($2)
        now=$(python3 -c 'import time; print(time.time())')
        case "$1" in
            !*) [ "$got" != "${1#!}" ] && hit=1 || hit=0 ;;
            *) [ "$got" = "$1" ] && hit=1 || hit=0 ;;
        esac
        if [ "$hit" = 1 ]; then
            python3 -c "print(f'{$now - $start:.2f}')"
            return 0
        fi
        if python3 -c "import sys; sys.exit(0 if $now - $start > $3 else 1)"; then
            echo "timeout(last=$got)"
            return 1
        fi
        sleep 0.2
    done
}

cleanup() {
    [ -n "${MLPA_PID:-}" ] && kill "$MLPA_PID" 2>/dev/null
    $COMPOSE unpause redis >/dev/null 2>&1
    $COMPOSE start redis >/dev/null 2>&1
    redis DEL "$FLAG_KEY" >/dev/null 2>&1
    for k in ${GUEST_KEY:-} ${OTHER_KEY:-}; do
        litellm_admin /key/delete "{\"keys\":[\"$k\"]}" >/dev/null
    done
}
trap cleanup EXIT

echo "== setup"
redis DEL "$FLAG_KEY" >/dev/null
GUEST_KEY=$(litellm_admin /key/generate '{"key_alias":"qa-guest-kill-switch-guest"}' | jq -r .key)
OTHER_KEY=$(litellm_admin /key/generate '{"key_alias":"qa-guest-kill-switch-other"}' | jq -r .key)
[ "$GUEST_KEY" != null ] && [ "$OTHER_KEY" != null ] || { echo "key/generate failed"; exit 1; }

PORT=$QA_PORT \
    MASTER_KEY=$MASTER_KEY \
    MLPA_GUEST_ENABLED=true \
    MLPA_GUEST_VIRTUAL_KEY=$GUEST_KEY \
    MLPA_GUEST_TOKEN_SIGNING_SECRET=qa-guest-secret \
    MLPA_ENFORCE_GUEST_CAP=false \
    ENABLE_TRAFFIC_CONTRACT_ENFORCEMENT=false \
    GUEST_KILL_SWITCH_CACHE_TTL_SECONDS=$TTL \
    uv run mlpa >"$LOG" 2>&1 &
MLPA_PID=$!
for _ in $(seq 60); do
    curl -sf "$MLPA/health/liveness" >/dev/null && break
    sleep 0.5
done
curl -sf "$MLPA/health/liveness" >/dev/null || { echo "MLPA didn't start, see $LOG"; exit 1; }
grep -q "Connected to Redis" "$LOG" && pass "Redis connected with traffic contracts off" \
    || fail "Redis not connected with traffic contracts off"
[ -n "${FXA_TOKEN:-}" ] && echo "FxA check: on" || echo "FxA check: off (set FXA_TOKEN to run it)"

echo "== 1. baseline"
check "guest token" 200 "$(guest_token_code)"
GUEST_TOKEN=$(jq -r .access_token /tmp/qa_body)
check "guest completion" 200 "$(guest_completion_code)"
check_non_guest "baseline"

echo "== 2. kill switch endpoint"
check "disable with wrong master key" 401 "$(flip disable wrong)"
check "flag untouched after 401" "" "$(redis GET "$FLAG_KEY")"
check "disable" 200 "$(flip disable)"
check "disable response" '{"guests_enabled":false}' "$(body)"
check "Redis flag" 0 "$(redis GET "$FLAG_KEY")"

echo "== 3. guests stopped, others keep working"
check "guest token after disable" 404 "$(guest_token_code)"
check "guest completion after disable" 401 "$(guest_completion_code)"
echo "      body: $(body)"
check_non_guest "disabled"
curl -s "$MLPA/metrics" | grep -q '^mlpa_guest_enabled 0.0' && pass "gauge mlpa_guest_enabled=0" \
    || fail "gauge mlpa_guest_enabled not 0"

echo "== 4. enable"
check "enable" 200 "$(flip enable)"
check "enable response" '{"guests_enabled":true}' "$(body)"
check "guest completion after enable" 200 "$(guest_completion_code)"

echo "== 5. time to effect for other pods (flag set in Redis, cache TTL=${TTL}s)"
redis SET "$FLAG_KEY" 0 >/dev/null
t=$(wait_for 401 guest_completion_code $((TTL + 5))) && pass "guest off after ${t}s" || fail "guest not off: $t"
redis SET "$FLAG_KEY" 1 >/dev/null
t=$(wait_for 200 guest_completion_code $((TTL + 5))) && pass "guest back after ${t}s" || fail "guest not back: $t"

echo "== 6. fail open: Redis down"
sleep "$TTL"
$COMPOSE stop redis >/dev/null 2>&1
sleep "$TTL"
check "guest token with Redis down" 200 "$(guest_token_code)"
grep -q "Guest kill switch read failed" "$LOG" && pass "Redis error logged" || fail "Redis error not logged"
$COMPOSE start redis >/dev/null 2>&1
for _ in $(seq 30); do redis PING 2>/dev/null | grep -q PONG && break; sleep 1; done
sleep "$TTL"

echo "== 6b. fail open: Redis hung (paused, connections hang instead of refusing)"
$COMPOSE pause redis >/dev/null 2>&1
sleep "$TTL"
t=$(wait_for 200 guest_token_code 3) && pass "guest token with Redis hung in ${t}s" \
    || fail "guest token with Redis hung: $t"
m=$(curl -s -o /dev/null -m 5 -w '%{http_code} %{time_total}' "$MLPA/metrics")
[ "${m%% *}" = 200 ] && pass "/metrics with Redis hung ($m s)" || fail "/metrics with Redis hung: $m"
$COMPOSE unpause redis >/dev/null 2>&1
for _ in $(seq 30); do redis PING 2>/dev/null | grep -q PONG && break; sleep 1; done
sleep "$TTL"

echo "== 7. LiteLLM /key/block on the guest key"
litellm_admin /key/block "{\"key\":\"$GUEST_KEY\"}" >/dev/null
t=$(wait_for '!200' guest_completion_code 30) && pass "guest completion blocked after ${t}s" \
    || fail "guest completion still 200 after key block: $t"
echo "      status: $(guest_completion_code)"
echo "      body: $(body)"
check "guest token still issued (key block doesn't stop creation)" 200 "$(guest_token_code)"
check_non_guest "key blocked"
litellm_admin /key/unblock "{\"key\":\"$GUEST_KEY\"}" >/dev/null
t=$(wait_for 200 guest_completion_code 30) && pass "guest back after unblock in ${t}s" \
    || fail "guest not back after unblock: $t"

echo
[ "$FAILED" = 0 ] && echo "ALL PASS" || echo "SOME CHECKS FAILED, MLPA log: $LOG"
exit "$FAILED"
