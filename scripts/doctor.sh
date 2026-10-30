#!/usr/bin/env bash
#
# Preflight for the unattended jobs. Run it after setup, and again whenever a
# run surprises you.
#
#   make doctor
#
# Everything here is something that works when you type it and fails at 03:00,
# which is the only interesting class of bug in a scheduled agent. Each check
# answers one question: would this still work with nobody watching?
#
# Exit status is the number of failures, so CI or another cron job can use it.

set -uo pipefail

pass=0; fail=0; warn=0
ok()   { printf '  \033[32mok\033[0m    %s\n' "$1"; pass=$((pass+1)); }
bad()  { printf '  \033[31mFAIL\033[0m  %s\n' "$1"; [ -n "${2:-}" ] && printf '        %s\n' "$2"; fail=$((fail+1)); }
note() { printf '  \033[33mwarn\033[0m  %s\n' "$1"; [ -n "${2:-}" ] && printf '        %s\n' "$2"; warn=$((warn+1)); }
head_() { printf '\n%s\n' "$1"; }

conf="${CANARY_CONF:-$HOME/.hermes/canary.conf}"
[ -r "$conf" ] && . "$conf"
clone="${CANARY_CLONE:-$PWD}"
scripts="${HERMES_SCRIPTS:-$HOME/.hermes/scripts}"

# ---------------------------------------------------------------- config
head_ "Config"

if [ -r "$conf" ]; then
  ok "$conf exists"
  [ -n "${CANARY_REPO:-}" ] && ok "CANARY_REPO=$CANARY_REPO" \
    || bad "CANARY_REPO not set in $conf" "run: make install"
  [ -n "${CANARY_CLONE:-}" ] && ok "CANARY_CLONE=$CANARY_CLONE" \
    || bad "CANARY_CLONE not set" "the agent will not know where the repo is; run: make install"
else
  bad "$conf missing" "run: make install"
fi

# -------------------------------------------------------------- installed
head_ "Installed scripts ($scripts)"
for f in commodities.sh quotes.py format_plain.jq \
         briefing-plain.sh briefing-raw.sh briefing-gate.sh \
         canary_gate.sh probe.py; do
  [ -e "$scripts/$f" ] && ok "$f" || bad "$f missing" "run: make install"
done

if [ -e "$scripts/quotes.py" ] && [ -e "$clone/scripts/quotes.py" ]; then
  if cmp -s "$scripts/quotes.py" "$clone/scripts/quotes.py"; then
    ok "installed copy matches the clone"
  else
    note "installed quotes.py differs from the clone" \
         "git pull changes the clone, not $scripts -- run: make install"
  fi
fi

# --------------------------------------------------------------- binaries
head_ "Binaries on a NON-interactive PATH"
# The scheduler does not source .bashrc, so this is the PATH a cron run sees.
cron_path="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
for b in python3 jq git; do
  if PATH="$cron_path" command -v "$b" >/dev/null 2>&1; then ok "$b"
  else bad "$b not on the cron PATH" "use an absolute path in the scripts"; fi
done

if PATH="$cron_path" command -v gh >/dev/null 2>&1; then
  ok "gh (on the cron PATH)"
elif [ -x "$HOME/.local/bin/gh" ]; then
  ok "gh at ~/.local/bin/gh (canary_gate.sh resolves this explicitly)"
else
  bad "gh not found" "install it, or the canary job can never read the issue"
fi

# ------------------------------------------------------------------- ssh
head_ "Push path (git over SSH)"
remote=$(git -C "$clone" remote get-url origin 2>/dev/null)

case "${remote:-}" in
  "")        bad "no origin remote in $clone" ;;
  https://*) bad "origin is HTTPS: $remote" \
                 "SSH is never invoked, so the deploy key is unused. Fix: git -C $clone remote set-url origin git@<alias>:owner/repo.git" ;;
  *)         ok "origin is SSH: $remote" ;;
esac

host=$(printf '%s' "${remote:-}" | sed -nE 's#^(ssh://)?git@([^:/]+).*#\2#p')
if [ -n "$host" ]; then
  if ssh-keygen -F "$host" >/dev/null 2>&1 \
     || ssh-keygen -F github.com >/dev/null 2>&1; then
    ok "host key for $host is in known_hosts"
  else
    bad "$host not in known_hosts" \
        "the first unattended connection will fail on the host-authenticity prompt. Fix: ssh-keyscan github.com >> ~/.ssh/known_hosts"
  fi

  key=$(ssh -G "$host" 2>/dev/null | awk '/^identityfile /{print $2; exit}')
  key="${key/#\~/$HOME}"
  if [ -n "$key" ] && [ -r "$key" ]; then
    ok "identity file $key"
    if ssh-keygen -y -P "" -f "$key" >/dev/null 2>&1; then
      ok "key has no passphrase (required: nobody is there to type one)"
    else
      bad "key is passphrase-protected" \
          "an unattended push cannot unlock it; use a passphrase-free deploy key"
    fi
  else
    note "could not resolve an identity file for $host"
  fi
fi

# The real test of write access, with no side effects.
if [ -n "${remote:-}" ]; then
  branch=$(git -C "$clone" rev-parse --abbrev-ref HEAD 2>/dev/null)
  if out=$(cd "$clone" && GIT_TERMINAL_PROMPT=0 \
             git push --dry-run origin "HEAD:refs/heads/$branch" 2>&1); then
    ok "push --dry-run succeeded (deploy key has write access)"
  else
    case "$out" in
      *"not been granted write access"*)
        bad "deploy key is READ-ONLY" \
            "GitHub cannot toggle this: delete the key and re-add it with 'Allow write access'" ;;
      *"Permission denied"*|*"publickey"*)
        bad "SSH auth failed" "check the alias and that the public half is on the repo" ;;
      *)
        bad "push --dry-run failed" "$(printf '%s' "$out" | head -2)" ;;
    esac
  fi
fi

# ----------------------------------------------------------------- token
head_ "GitHub token (API, separate from the push path)"
GH="${GH_BIN:-$(command -v gh 2>/dev/null || echo "$HOME/.local/bin/gh")}"
tok="${GH_TOKEN_FILE:-$HOME/.hermes/secrets/gh_token}"

if [ -r "$tok" ]; then
  perm=$(stat -c '%a' "$tok" 2>/dev/null || stat -f '%A' "$tok" 2>/dev/null)
  [ "$perm" = "600" ] && ok "token file mode 600" \
    || note "token file mode is $perm" "chmod 600 $tok"
else
  bad "no token at $tok"
fi

if [ -x "$GH" ] && [ -n "${CANARY_REPO:-}" ]; then
  export GH_TOKEN="$(cat "$tok" 2>/dev/null)"
  "$GH" issue list --repo "$CANARY_REPO" --limit 1 >/dev/null 2>&1 \
    && ok "can read issues" || bad "cannot read issues" "needs Issues: Read"
  "$GH" api "repos/$CANARY_REPO/actions/runs?per_page=1" >/dev/null 2>&1 \
    && ok "can read Actions runs (artifact download)" \
    || bad "cannot read Actions" "needs Actions: Read"
  "$GH" api "repos/$CANARY_REPO/pulls?per_page=1" >/dev/null 2>&1 \
    && ok "can read pull requests" || note "cannot read pull requests" \
       "gh pr create needs Pull requests: Read and write"
fi

# ---------------------------------------------------------------- summary
head_ "Summary"
printf '  %d ok, %d warn, %d FAIL\n\n' "$pass" "$warn" "$fail"
if [ "$fail" -gt 0 ]; then
  echo "  Fix the failures above before trusting a scheduled run."
else
  echo "  Nothing here will stop an unattended run."
fi
exit "$fail"
