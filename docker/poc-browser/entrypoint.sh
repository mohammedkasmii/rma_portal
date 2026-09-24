#!/bin/sh
# Container entrypoint: validate the environment, write the VNC password file,
# clear stale X locks (container *restart* keeps /tmp), then run supervisord.
set -eu

RUN_DIR=/tmp/rma-poc
PASSFILE="$RUN_DIR/vncpass"

if [ -z "${POC_VNC_PASSWORD:-}" ]; then
    echo "entrypoint: POC_VNC_PASSWORD is not set (noVNC would be open on the LAN)." >&2
    exit 1
fi

for dir in /var/lib/rma-poc/profile /var/lib/rma-poc/state; do
    if [ ! -w "$dir" ]; then
        echo "entrypoint: $dir is not writable by uid $(id -u); check the volume ownership." >&2
        exit 1
    fi
done

mkdir -p "$RUN_DIR"
chmod 700 "$RUN_DIR"
# printf is a shell builtin: the password never appears in a process argument list.
umask 077
printf '%s\n' "$POC_VNC_PASSWORD" > "$PASSFILE"
unset POC_VNC_PASSWORD

# A restarted container keeps its filesystem, so a previous Xvfb lock survives.
rm -f /tmp/.X99-lock /tmp/.X11-unix/X99

exec /usr/bin/supervisord -n -c /etc/rma-poc/supervisord.conf
