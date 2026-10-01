#!/usr/bin/env bash
# Run the scenario under macOS sandbox-exec: writes outside the sandbox folder fail, so your real
# ~/.claude cannot change even if bondi had a bug.
set -euo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
SANDBOX="$(mktemp -d "${TMPDIR:-/tmp}/bondi-sandbox.XXXXXX")"
SANDBOX="$(cd "$SANDBOX" && pwd -P)"
# Build the tool env before locking writes; the sandbox then only runs it.
(cd "$here" && uv sync --quiet)
BONDI="$here/.venv/bin/bondi"
profile="$(cat <<EOF
(version 1)
(allow default)
(deny file-write*)
(allow file-write* (subpath "$SANDBOX") (literal "/dev/null") (literal "/dev/tty") (regex #"^/dev/fd/"))
EOF
)"
echo "sandbox: $SANDBOX (writes allowed only here)"
sandbox-exec -p "$profile" env SANDBOX="$SANDBOX" BONDI="$BONDI" PYTHONDONTWRITEBYTECODE=1 bash "$here/sandbox/scenario.sh"
echo "== proof the lock works: a write to the real home is refused"
if sandbox-exec -p "$profile" bash -c "touch '$HOME/.bondi-sandbox-probe'" 2>/dev/null; then
  rm -f "$HOME/.bondi-sandbox-probe"; echo "FAIL  sandbox allowed a write to \$HOME"; exit 1
fi
echo "PASS  write to \$HOME refused inside the sandbox"
