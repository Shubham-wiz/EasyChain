#!/usr/bin/env bash
# Run a flow through the Compose stack: the API queues it, a worker runs it.
set -euo pipefail
BASE=${BASE:-http://localhost:8000}
flow=$(curl -sf -X POST "$BASE/api/flows" -H 'Content-Type: application/json' -d '{
  "spec": {
    "name": "Compose smoke test",
    "steps": [
      {"id": "input", "type": "input", "settings": {"fields": [{"name": "text"}]}},
      {"id": "shout", "type": "code", "settings": {"code": "def run(data):\n    return {\"loud\": data[\"text\"].upper()}\n"}},
      {"id": "output", "type": "output", "settings": {"fields": ["loud"]}}
    ],
    "connections": [{"from": "input", "to": "shout"}, {"from": "shout", "to": "output"}]
  }
}' | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')
run=$(curl -sf -X POST "$BASE/api/runs" -H 'Content-Type: application/json' \
  -d "{\"flow_id\": \"$flow\", \"inputs\": {\"text\": \"hello\"}, \"background\": true}" \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["run_id"])')
for _ in $(seq 1 60); do
  status=$(curl -sf "$BASE/api/runs/$run" | python3 -c 'import json,sys; print(json.load(sys.stdin)["status"])')
  [ "$status" = ok ] && break
  sleep 1
done
curl -sf "$BASE/api/runs/$run" | python3 -c '
import json, sys
run = json.load(sys.stdin)
assert run["status"] == "ok", run
assert run["output"] == {"loud": "HELLO"}, run
print("Compose stack ran the flow:", run["output"])'
