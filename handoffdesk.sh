#!/bin/sh
set -eu
APP_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
PYTHON=${HANDOFFDESK_PYTHON:-python3}
if ! command -v "$PYTHON" >/dev/null 2>&1; then
    printf '%s\n' 'HandoffDesk requires Python 3.10+. Set HANDOFFDESK_PYTHON to its executable.' >&2
    exit 2
fi
if ! "$PYTHON" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 2)'; then
    printf '%s\n' 'HandoffDesk requires Python 3.10 or newer.' >&2
    exit 2
fi
cd "$APP_DIR"
exec "$PYTHON" -s -B -X utf8 -m handoffdesk "$@"
