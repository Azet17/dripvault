#!/usr/bin/env bash
# DripVault pre-flight — clone fresh, run every README step, verify outputs.
# This is what a screener does. Run it before submitting.
set -u
REPO_URL="${1:-https://github.com/Azet17/dripvault.git}"
TS=$(date +%Y%m%d-%H%M%S)
DIR="/tmp/dripvault-preflight-$TS"
PASS=0; FAIL=0

ok()   { PASS=$((PASS+1)); echo "  ✅ $1"; }
bad()  { FAIL=$((FAIL+1)); echo "  ❌ $1"; }
check(){ if [ "$2" = "$3" ]; then ok "$1 (got $2)"; else bad "$1 (want $3, got $2)"; fi; }

echo "== DripVault pre-flight: $REPO_URL =="
echo "clone → $DIR"
git clone -q "$REPO_URL" "$DIR" || { echo "❌ clone failed"; exit 1; }
cd "$DIR" || exit 1

echo "— step 1: live dry-run quote (eth usdc → near)"
OUT=$(python3 scripts/quote.py --from eth --token usdc --amount 10 --to near --asset near 2>&1); RC=$?
check "exit code" "$RC" "0"
echo "$OUT" | grep -q '"mode": "DRY-RUN (nothing executed)"' && ok "mode=DRY-RUN" || bad "mode=DRY-RUN missing"
echo "$OUT" | grep -q 'amountOutFormatted' && ok "amountOutFormatted present" || bad "amountOutFormatted missing"

echo "— step 2: slippage guard (max-slippage 0)"
python3 scripts/quote.py --from eth --token usdc --amount 10 --to near --asset near --max-slippage 0 >/dev/null 2>&1
check "exit code" "$?" "2"

echo "— step 3: no-liquidity example (base usdc → btc \$25; outcome may differ by day)"
python3 scripts/quote.py --from base --token usdc --amount 25 --to btc --asset btc >/dev/null 2>&1
RC=$?
if [ "$RC" = "0" ] || [ "$RC" = "1" ]; then ok "route resolved honestly (exit $RC — 1=no liquidity, 0=quoted)"; else bad "unexpected exit $RC"; fi

echo "— step 4: schedule planner"
python3 scripts/plan.py --total 300 --drips 10 --interval 24h --slippage 1.0 >/dev/null 2>&1
check "exit code" "$?" "0"
python3 - <<'PY' && ok "plan JSON: 10 drips × 30.0" || bad "plan JSON wrong"
import json,sys
d=json.load(open("history.json")) if False else json.loads(__import__("subprocess").run(["python3","scripts/plan.py","--total","300","--drips","10","--interval","24h"],capture_output=True,text=True).stdout)
sys.exit(0 if d["drips"]==10 and abs(d["perDrip"]-30.0)<1e-9 else 1)
PY

echo "— step 5: dry leg + wet refusal"
python3 scripts/scheduler.py --from eth --token usdc --amount 10 --to near --asset near >/dev/null 2>&1
check "dry leg exit" "$?" "0"
python3 -c "import json;h=json.load(open('history.json'));exit(0 if h[-1]['status']=='DRY_RUN' else 1)" && ok "leg recorded DRY_RUN" || bad "leg not recorded"
python3 scripts/scheduler.py --from eth --token usdc --amount 10 --to near --asset near --wet >/dev/null 2>&1
check "wet refusal exit" "$?" "6"
python3 -c "import json;h=json.load(open('history.json'));exit(0 if h[-1]['status']=='WET_RUN_REFUSED_NO_CREDENTIALS' else 1)" && ok "refusal recorded" || bad "refusal not recorded"

echo "— step 6: interval loop (2 capped legs)"
python3 scripts/scheduler.py --from eth --token usdc --amount 5 --to near --asset near --interval 0.0003 --max-legs 2 >/dev/null 2>&1
check "loop exit" "$?" "0"
python3 -c "import json;h=json.load(open('history.json'));exit(0 if len(h)==4 else 1)" && ok "history: 4 legs total" || bad "history count wrong"

echo "— step 7: web UI"
setsid nohup python3 scripts/ui_server.py 8799 >/dev/null 2>&1 < /dev/null &
UI_PID=$!
sleep 2
curl -s -o /dev/null -m 5 http://127.0.0.1:8799/ ; check "GET / " "$?" "0"
curl -s -m 5 http://127.0.0.1:8799/health | grep -q '"ok": true' && ok "health ok" || bad "health fail"
curl -s -m 5 http://127.0.0.1:8799/history.json | python3 -c "import json,sys;exit(0 if len(json.load(sys.stdin))==4 else 1)" && ok "history served via UI" || bad "history via UI fail"
kill "$UI_PID" 2>/dev/null

echo
echo "== RESULT: $PASS passed, $FAIL failed =="
[ "$FAIL" = "0" ] && echo "PRE-FLIGHT GREEN — repo matches README end-to-end" || echo "PRE-FLIGHT RED — fix before submit"
exit "$FAIL"
