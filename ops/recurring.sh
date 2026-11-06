#!/usr/bin/env bash
set -euo pipefail
# No Google credentials are required for public catalog/lab/literature checks.
# Private Google reads are optional and must be enabled in the private source config.
: "${GLO_STATE_DIR:?Set GLO_STATE_DIR to a private persistent state directory}"
umask 077
mkdir -p "$GLO_STATE_DIR/imports" "$GLO_STATE_DIR/backups" "$GLO_STATE_DIR/reports"
exec 9>"$GLO_STATE_DIR/check.lock"
flock -n 9 || exit 0
stamp=$(date -u +%Y%m%dT%H%M%SZ)
glo_python=${GLO_PYTHON:-python3}
# Exit status 2 means some sources could not be checked. Retain the report and backup.
check_status=0
"$glo_python" -m gloformulas.cli --db "$GLO_STATE_DIR/reference.sqlite" daily --live --state "$GLO_STATE_DIR" || check_status=$?
"$glo_python" - "$GLO_STATE_DIR/reference.sqlite" "$GLO_STATE_DIR/backups/$stamp.sqlite" <<'PY'
import sqlite3,sys
with sqlite3.connect(sys.argv[1]) as source, sqlite3.connect(sys.argv[2]) as target:
    source.backup(target)
PY
exit "$check_status"
