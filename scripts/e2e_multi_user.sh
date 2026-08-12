#!/usr/bin/env bash
# Live end-to-end check of the multi-user authorization story against a real
# running mimic-tts server. This is deliberately NOT a substitute for
# server/tests/test_authorization_matrix.py -- it exists because that suite
# only proves the FastAPI TestClient's in-process view of the app is
# correct, not that a real deployment (real curl, real network, real
# uvicorn) behaves the same way.
#
# Usage:
#   MIMIC_URL=https://mimic.example.com MIMIC_ADMIN_TOKEN=mk_xxx \
#     scripts/e2e_multi_user.sh
#
# Safe to run against a live server with real user data: everything this
# script creates (a scratch admin voice, a scratch friend key) is namespaced
# under "e2e-" and purged at the end, including on failure (see `trap`
# below). It never touches any pre-existing key or voice -- EXCEPT: if a
# voice literally named "e2e-admin-secret" already exists for the admin
# key, `POST /clone/register` overwrites its audio/transcript in place (the
# same behavior as any other re-register of an existing name). The
# namespacing is meant to make that collision practically impossible, not
# to guard against someone deliberately naming a real voice that.
set -euo pipefail

: "${MIMIC_URL:?set MIMIC_URL, e.g. https://mimic.example.com}"
: "${MIMIC_ADMIN_TOKEN:?set MIMIC_ADMIN_TOKEN to an admin key}"

for bin in curl jq base64; do
  command -v "$bin" >/dev/null || { echo "missing required tool: $bin" >&2; exit 2; }
done

URL="${MIMIC_URL%/}"
FRIEND_LABEL="e2e-friend"
ADMIN_VOICE_NAME="e2e-admin-secret"

# 3.2 KB, 0.1s of 16kHz mono silence -- a real WAV ffmpeg will decode, not a
# placeholder that only happens to pass a byte-count check.
WAV_B64="UklGRqQMAABXQVZFZm10IBAAAAABAAEAgD4AAAB9AAACABAAZGF0YYAMAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=="

RESET=$'\033[0m'; RED=$'\033[31m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'; BOLD=$'\033[1m'
pass() { printf '%s[PASS]%s %s\n' "$GREEN" "$RESET" "$1"; }
# stderr, not stdout: every expect_status call site redirects its stdout to
# /dev/null (to swallow the echoed status code on success), which would
# silently eat the one message that explains a failure if fail() wrote
# there too.
fail() { printf '%s[FAIL]%s %s\n' "$RED" "$RESET" "$1" >&2; exit 1; }
warn() { printf '%s[WARN]%s %s\n' "$YELLOW" "$RESET" "$1" >&2; }
step() { printf '\n%s== %s ==%s\n' "$BOLD" "$1" "$RESET"; }

# Body preview for error messages: strip NULs (a binary WAV body otherwise
# triggers "ignored null byte in input" from bash's command substitution
# and can garble the terminal) and cap the length so a large body doesn't
# flood the output.
body_preview() {
  tr -d '\000' < "$1" | head -c 500
}

WORKDIR="$(mktemp -d)"
FRIEND_TOKEN=""
ADMIN_VOICE_REGISTERED=0

cleanup() {
  # Runs on every exit path (success, failure, or Ctrl-C) so a script that
  # dies partway through never leaves scratch state on a real server. A
  # failed cleanup call must not be silent -- it's the difference between
  # "nothing left behind" and "a scratch key or voice is still sitting on
  # the owner's production server and nobody knows."
  set +e
  local status
  if [[ -n "$FRIEND_TOKEN" ]]; then
    status="$(curl -s -o /dev/null -w '%{http_code}' -X DELETE \
      -H "Authorization: Bearer ${MIMIC_ADMIN_TOKEN}" \
      "${URL}/admin/keys/${FRIEND_LABEL}?purge=true")"
    [[ "$status" == "200" ]] || warn "cleanup: purging key '${FRIEND_LABEL}' returned $status -- remove it by hand"
  fi
  if [[ "$ADMIN_VOICE_REGISTERED" == "1" ]]; then
    # Qualified, not bare: an admin's bare-name lookup falls through to
    # "the single visible voice by that name" once the admin's own copy is
    # gone, which could match a different key's identically-named voice in
    # a pathological case. The qualified form always means exactly this one.
    status="$(curl -s -o /dev/null -w '%{http_code}' -X DELETE \
      -H "Authorization: Bearer ${MIMIC_ADMIN_TOKEN}" \
      "${URL}/clone/voices/${ADMIN_VOICE_OWNER:-unknown-owner}/${ADMIN_VOICE_NAME}")"
    [[ "$status" == "200" ]] || warn "cleanup: deleting voice '${ADMIN_VOICE_OWNER:-unknown-owner}/${ADMIN_VOICE_NAME}' returned $status -- remove it by hand"
  fi
  rm -rf "$WORKDIR"
}
trap cleanup EXIT

# expect_status METHOD PATH EXPECTED_STATUS [curl-args...]
# Writes the response body to $WORKDIR/last_body and echoes it on failure.
expect_status() {
  local method="$1" path="$2" expected="$3"
  shift 3
  local status
  status="$(curl -s -o "${WORKDIR}/last_body" -w '%{http_code}' -X "$method" "${URL}${path}" "$@")"
  if [[ "$status" != "$expected" ]]; then
    fail "$method $path -> $status (expected $expected). Body: $(body_preview "${WORKDIR}/last_body")"
  fi
  echo "$status"
}

step "0. Register a scratch private voice as admin"
printf '%s' "$WAV_B64" | base64 -d > "${WORKDIR}/ref.wav"
expect_status POST /clone/register 200 \
  -H "Authorization: Bearer ${MIMIC_ADMIN_TOKEN}" \
  -F "name=${ADMIN_VOICE_NAME}" \
  -F "ref_text=hello from the e2e script" \
  -F "ref_audio=@${WORKDIR}/ref.wav;type=audio/wav" >/dev/null
ADMIN_VOICE_REGISTERED=1
ADMIN_VOICE_OWNER="$(curl -s -H "Authorization: Bearer ${MIMIC_ADMIN_TOKEN}" "${URL}/me" | jq -r .label)"
if [[ -z "$ADMIN_VOICE_OWNER" || "$ADMIN_VOICE_OWNER" == "null" ]]; then
  ADMIN_VOICE_OWNER=""  # so cleanup's ${ADMIN_VOICE_OWNER:-unknown-owner} fallback triggers
  fail "could not determine admin's own label from GET /me"
fi
pass "admin voice ${ADMIN_VOICE_OWNER}/${ADMIN_VOICE_NAME} registered"

step "1. Mint a friend key"
expect_status POST /admin/keys 200 \
  -H "Authorization: Bearer ${MIMIC_ADMIN_TOKEN}" \
  -H "Content-Type: application/json" \
  -d "{\"label\": \"${FRIEND_LABEL}\"}" >/dev/null
FRIEND_TOKEN="$(jq -r .token < "${WORKDIR}/last_body")"
[[ -n "$FRIEND_TOKEN" && "$FRIEND_TOKEN" != "null" ]] || fail "no token in mint response"
pass "minted ${FRIEND_LABEL}"

step "2. Friend identity check (GET /me)"
expect_status GET /me 200 -H "Authorization: Bearer ${FRIEND_TOKEN}" >/dev/null
ROLE="$(jq -r .role < "${WORKDIR}/last_body")"
[[ "$ROLE" == "user" ]] || fail "expected role=user, got $ROLE"
pass "friend authenticates as role=user"

step "3. Friend registers their own voice"
expect_status POST /clone/register 200 \
  -H "Authorization: Bearer ${FRIEND_TOKEN}" \
  -F "name=friendvoice" \
  -F "ref_text=hello from the friend" \
  -F "ref_audio=@${WORKDIR}/ref.wav;type=audio/wav" >/dev/null
pass "friend registered ${FRIEND_LABEL}/friendvoice"

step "4. Friend synthesizes with their own voice"
expect_status POST /clone/tts 200 \
  -H "Authorization: Bearer ${FRIEND_TOKEN}" \
  -F "text=hi there" \
  -F "name=friendvoice" >/dev/null
head -c 4 "${WORKDIR}/last_body" | grep -q RIFF || fail "own-voice synth did not return a WAV"
pass "friend's own-voice synth returned audio"

step "5. Friend's voice listing must not contain the admin voice"
expect_status GET /clone/voices 200 -H "Authorization: Bearer ${FRIEND_TOKEN}" >/dev/null
if grep -q "${ADMIN_VOICE_OWNER}/${ADMIN_VOICE_NAME}" "${WORKDIR}/last_body"; then
  fail "friend's /clone/voices leaked the admin-owned private voice"
fi
pass "admin voice absent from friend's listing"

step "6. DENIED: friend synth against the admin-owned private voice (expect 404)"
expect_status POST /clone/tts 404 \
  -H "Authorization: Bearer ${FRIEND_TOKEN}" \
  -F "text=hi" \
  -F "name=${ADMIN_VOICE_OWNER}/${ADMIN_VOICE_NAME}" >/dev/null
pass "${BOLD}pre-grant: friend correctly denied (404)${RESET}"

step "7. Admin grants the friend access"
expect_status POST "/clone/voices/${ADMIN_VOICE_OWNER}/${ADMIN_VOICE_NAME}/grants" 200 \
  -H "Authorization: Bearer ${MIMIC_ADMIN_TOKEN}" \
  -H "Content-Type: application/json" \
  -d "{\"grantee\": \"${FRIEND_LABEL}\"}" >/dev/null
pass "grant issued to ${FRIEND_LABEL}"

step "8. ALLOWED: same synth call now succeeds (expect 200)"
expect_status POST /clone/tts 200 \
  -H "Authorization: Bearer ${FRIEND_TOKEN}" \
  -F "text=hi" \
  -F "name=${ADMIN_VOICE_OWNER}/${ADMIN_VOICE_NAME}" >/dev/null
pass "${BOLD}post-grant: friend now allowed (200)${RESET}"

step "9. Admin revokes the friend key"
expect_status DELETE "/admin/keys/${FRIEND_LABEL}" 200 \
  -H "Authorization: Bearer ${MIMIC_ADMIN_TOKEN}" >/dev/null
pass "friend key revoked"

step "10. Revoked friend token is rejected"
expect_status GET /me 401 -H "Authorization: Bearer ${FRIEND_TOKEN}" >/dev/null
pass "revoked token gets 401"

step "11. Cleanup"
expect_status DELETE "/admin/keys/${FRIEND_LABEL}?purge=true" 200 \
  -H "Authorization: Bearer ${MIMIC_ADMIN_TOKEN}" >/dev/null
FRIEND_TOKEN=""  # already purged; skip the redundant purge in the EXIT trap
pass "friend key purged"

printf '\n%s%sAll steps passed. Steps 6 (denied) and 8 (allowed) are the whole feature.%s\n' \
  "$BOLD" "$GREEN" "$RESET"
