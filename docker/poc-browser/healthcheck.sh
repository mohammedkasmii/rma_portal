#!/bin/sh
# Healthy = X display up, VNC server listening, noVNC page served.
set -eu

[ -S /tmp/.X11-unix/X99 ]
pgrep -x Xvfb >/dev/null
pgrep -x x11vnc >/dev/null
curl -fsS --max-time 3 -o /dev/null http://127.0.0.1:6080/vnc.html
