#!/bin/bash
LOG=/c/Users/rajac/OneDrive/Desktop/Python/Finance_Model/reports/redteam_20260501/curl_log.jsonl
BASE=http://localhost:8002
BODY=/c/Users/rajac/OneDrive/Desktop/Python/Finance_Model/reports/redteam_20260501/_body.txt

log_curl() {
  local step="$1" method="$2" path="$3" body="$4"
  local url="$BASE$path"
  local t0=$(date +%s%N)
  local resp
  if [ -z "$body" ]; then
    resp=$(curl -s -o "$BODY" -w "%{http_code}" -X "$method" "$url")
  else
    resp=$(curl -s -o "$BODY" -w "%{http_code}" -X "$method" -H "Content-Type: application/json" -d "$body" "$url")
  fi
  local t1=$(date +%s%N)
  local ms=$(( (t1 - t0) / 1000000 ))
  STEP="$step" METHOD="$method" URL="$url" STATUS="$resp" MS="$ms" BODY="$BODY" LOG="$LOG" python <<'PYEOF'
import os, json, sys
body = open(os.environ['BODY'],'rb').read().decode('utf-8','replace').replace('\n',' ')
excerpt = body[:600]
rec = {
  "step": os.environ['STEP'],
  "method": os.environ['METHOD'],
  "url": os.environ['URL'],
  "status": int(os.environ['STATUS']),
  "ms": int(os.environ['MS']),
  "body_excerpt": excerpt,
}
with open(os.environ['LOG'],'a') as fh:
    fh.write(json.dumps(rec) + "\n")
# print clean summary
try:
    d = json.loads(body)
    if isinstance(d, dict) and 'report_html' in d:
        d = {k:v for k,v in d.items() if k != 'report_html'}
    print("[" + os.environ['STEP'] + "] " + os.environ['METHOD'] + " " + os.environ['URL'] + " -> " + os.environ['STATUS'] + " (" + os.environ['MS'] + "ms)")
    print(json.dumps(d, default=str)[:1200])
except Exception:
    print("[" + os.environ['STEP'] + "] -> " + os.environ['STATUS'] + " (" + os.environ['MS'] + "ms): " + body[:400])
PYEOF
}
export -f log_curl
export LOG BASE BODY
