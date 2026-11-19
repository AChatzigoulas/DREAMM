#!/bin/sh
set -eu

if ! command -v python3 >/dev/null 2>&1; then
    echo 'DREAMM needs your distribution Python 3.9 or later to install.' >&2
    exit 2
fi
root=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
exec python3 -I -S -B "$root/tools/install.py" "$@"
