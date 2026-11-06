#!/usr/bin/env bash
set -euo pipefail
# Run from the checkout. State stays on a private persistent volume, never in git.
: "${GLO_STATE_DIR:?Set GLO_STATE_DIR to a private persistent state directory}"
umask 077
mkdir -p "$GLO_STATE_DIR/imports" "$GLO_STATE_DIR/backups" "$GLO_STATE_DIR/reports"
exec 9>"$GLO_STATE_DIR/check.lock"
flock -n 9 || exit 0
stamp=$(date -u +%Y%m%dT%H%M%SZ)
# Credentials are supplied by ADC/workload identity; do not echo environment variables.
python -m gloformulas.cli google-sync --directory "$GLO_STATE_DIR/imports" > "$GLO_STATE_DIR/reports/$stamp-sync.json"
# Optional normalized catalog ingestion. A trusted adapter must assert completeness.
# Feed is a local file produced by the site-specific crawler; never treat a crawl failure as empty.
if [[ -n "${GLO_CATALOG_FILE:-}" ]]; then
  cp -- "$GLO_CATALOG_FILE" "$GLO_STATE_DIR/imports/catalog.json"
else
  rm -f -- "$GLO_STATE_DIR/imports/catalog.json"
fi
python -m gloformulas.cli --db "$GLO_STATE_DIR/reference.sqlite" check --imports "$GLO_STATE_DIR/imports" --report "$GLO_STATE_DIR/reports/$stamp.json"
python -m gloformulas.cli --db "$GLO_STATE_DIR/reference.sqlite" guide "$GLO_STATE_DIR/reference-guide.md"
python - "$GLO_STATE_DIR/reference.sqlite" "$GLO_STATE_DIR/backups/$stamp.sqlite" <<'PY'
import sqlite3,sys
with sqlite3.connect(sys.argv[1]) as source, sqlite3.connect(sys.argv[2]) as target:
    source.backup(target)
PY
# Publication is intentionally a separate reviewed operation.
