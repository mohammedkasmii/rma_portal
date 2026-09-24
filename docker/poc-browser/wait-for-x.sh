#!/bin/sh
# Usage: wait-for-x.sh <command> [args...]
# Waits (bounded) for the Xvfb socket, then execs the command.
set -eu

i=0
while [ ! -S /tmp/.X11-unix/X99 ]; do
    i=$((i + 1))
    if [ "$i" -gt 60 ]; then
        echo "wait-for-x: X display :99 did not appear" >&2
        exit 1
    fi
    sleep 0.5
done
exec "$@"
