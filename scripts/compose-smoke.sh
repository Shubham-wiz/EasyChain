#!/usr/bin/env bash
# Run a flow through the Compose stack: the API queues it, a worker runs it.
# Sign-in is on in Docker, so this makes an Owner and an API key with the command line first
# (or uses EASYCHAIN_KEY if it is set). COMPOSE is how to reach the stack's containers.
set -euo pipefail
BASE=${BASE:-http://localhost:8000}
COMPOSE=${COMPOSE:-docker compose}

code=$(curl -s -o /dev/null -w '%{http_code}' "$BASE/api/flows")
[ "$code" = 401 ] || { echo "Expected the API to ask for sign-in (401), got $code"; exit 1; }
if [ -z "${EASYCHAIN_KEY:-}" ]; then
  email="smoke-$(date +%s)@example.com"
  echo "a smoke test password" \
    | $COMPOSE exec -T easychain easychain users add "$email" --role owner --password-stdin
  EASYCHAIN_KEY=$($COMPOSE exec -T easychain easychain keys create "$email" --name "smoke test" | tr -d '\r')
fi
curl() { command curl -H "Authorization: Bearer $EASYCHAIN_KEY" "$@"; }
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

# With docker-compose.sandbox.yml, Code steps run in a sandbox container as an unprivileged
# user with no network; check that a step really ran there.
if [ "${EXPECT_SANDBOX:-0}" = 1 ]; then
  curl -sf "$BASE/api/sandbox/status" | grep -q '"effective":"docker"'
  flow=$(curl -sf -X POST "$BASE/api/flows" -H 'Content-Type: application/json' -d '{
    "spec": {
      "name": "Compose sandbox test",
      "steps": [
        {"id": "input", "type": "input", "settings": {"fields": [{"name": "text"}]}},
        {"id": "probe", "type": "code", "settings": {"code": "import os, socket\n\ndef run(data):\n    try:\n        socket.create_connection((\"1.1.1.1\", 53), timeout=3)\n        net = \"open\"\n    except OSError:\n        net = \"blocked\"\n    return {\"uid\": os.getuid(), \"net\": net, \"data\": os.path.exists(\"/data\")}\n"}},
        {"id": "output", "type": "output", "settings": {"fields": ["uid", "net", "data"]}}
      ],
      "connections": [{"from": "input", "to": "probe"}, {"from": "probe", "to": "output"}]
    }
  }' | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')
  run=$(curl -sf -X POST "$BASE/api/runs" -H 'Content-Type: application/json' \
    -d "{\"flow_id\": \"$flow\", \"inputs\": {\"text\": \"x\"}, \"background\": true}" \
    | python3 -c 'import json,sys; print(json.load(sys.stdin)["run_id"])')
  for _ in $(seq 1 120); do
    status=$(curl -sf "$BASE/api/runs/$run" | python3 -c 'import json,sys; print(json.load(sys.stdin)["status"])')
    [ "$status" = ok ] || [ "$status" = error ] && break
    sleep 1
  done
  curl -sf "$BASE/api/runs/$run" | python3 -c '
import json, sys
run = json.load(sys.stdin)
assert run["status"] == "ok", run
assert run["output"] == {"uid": 65534, "net": "blocked", "data": False}, run
print("The Code step ran in the sandbox:", run["output"])'
fi

