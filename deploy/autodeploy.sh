#!/usr/bin/env bash
#
# Pull main, prove it works, then restart. Roll back if it doesn't.
#
# Run by indus-autodeploy.timer every few minutes. The server polls outward, so
# nothing new is exposed: no inbound port, no webhook, and the SSH allowlist
# stays as tight as it is.
#
# The test suite is the gate. It runs in about five seconds on this box, which
# makes it essentially free insurance against a bad merge reaching the lawyers.
# Anything that fails, at any stage, returns the box to the commit it was on.

set -uo pipefail

REPO="${HOME}/agent"
DEPLOY_KEY="${HOME}/.ssh/indus_deploy"
SEND_API="http://127.0.0.1:8601/send"
HEALTH="http://127.0.0.1:8600/health"
LOCK="/tmp/indus-autodeploy.lock"

log() { echo "[autodeploy] $*"; }

# Failures are announced in the firm group: a silent rollback is a bot that is
# quietly out of date, which is worse than a moment of noise.
notify() {
  curl -s -m 10 -X POST -H 'Content-Type: application/json' \
    -d "$(printf '{"Text": %s}' "$(python3 -c 'import json,sys; print(json.dumps(sys.argv[1]))' "$1")")" \
    "$SEND_API" >/dev/null 2>&1 || log "could not reach the send API to notify"
}

exec 9>"$LOCK"
flock -n 9 || { log "another run is in progress"; exit 0; }

cd "$REPO" || { log "no repo at $REPO"; exit 1; }
export GIT_SSH_COMMAND="ssh -i ${DEPLOY_KEY} -o StrictHostKeyChecking=accept-new -o IdentitiesOnly=yes"

git fetch --quiet origin main || { log "fetch failed (network?), will retry next tick"; exit 0; }

BEFORE="$(git rev-parse HEAD)"
AFTER="$(git rev-parse origin/main)"
[ "$BEFORE" = "$AFTER" ] && exit 0

SHORT="$(git rev-parse --short "$AFTER")"
SUBJECT="$(git log -1 --format=%s "$AFTER")"
log "new commit ${SHORT}: ${SUBJECT}"

CHANGED="$(git diff --name-only "$BEFORE" "$AFTER")"
GATEWAY_TOUCHED=0
grep -q '^gateway/' <<<"$CHANGED" && GATEWAY_TOUCHED=1

# Refuse anything that isn't a clean fast-forward rather than resolving it here.
if ! git merge --ff-only --quiet origin/main; then
  log "not a fast-forward, refusing"
  notify "Indus Bot: deploy of ${SHORT} skipped, main is not a fast-forward. Needs a look."
  exit 1
fi

rollback() {
  log "rolling back to ${BEFORE}"
  git checkout --quiet --force "$BEFORE"
  [ -f gateway/gateway.old ] && mv -f gateway/gateway.old gateway/gateway
  sudo systemctl restart indus-agent
  [ "$GATEWAY_TOUCHED" -eq 1 ] && sudo systemctl restart indus-gateway
  notify "Indus Bot: deploy of ${SHORT} failed ($1) and was rolled back. The bot is still running the previous version."
}

if ! .venv/bin/python -m pytest -q >/tmp/autodeploy-tests.log 2>&1; then
  log "tests failed:"; tail -5 /tmp/autodeploy-tests.log
  rollback "tests failed"
  exit 1
fi
log "tests pass"

# Only touch the Go binary when the gateway actually changed: rebuilding it
# means restarting WhatsApp, and an agent-only change has no business
# disturbing a live session.
if [ "$GATEWAY_TOUCHED" -eq 1 ]; then
  log "gateway changed, rebuilding"
  if ! (cd gateway && go build -o gateway.new .); then
    rollback "gateway build failed"
    exit 1
  fi
  mv -f gateway/gateway gateway/gateway.old
  mv -f gateway/gateway.new gateway/gateway
fi

sudo systemctl restart indus-agent
[ "$GATEWAY_TOUCHED" -eq 1 ] && sudo systemctl restart indus-gateway
sleep 12

if [ "$(curl -s -o /dev/null -w '%{http_code}' -m 10 "$HEALTH")" != "200" ]; then
  log "health check failed after restart"
  rollback "the agent did not come back healthy"
  exit 1
fi

log "deployed ${SHORT} successfully"
