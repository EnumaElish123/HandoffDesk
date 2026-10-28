#!/bin/sh
set -eu
APP_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
exec "$APP_DIR/handoffdesk.sh" "$@" tui
