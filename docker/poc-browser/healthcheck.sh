#!/bin/sh
# Healthy = X display up, VNC server up, noVNC page served, and the Camoufox
# browser executable resolves. Never launches a browser.
set -eu

[ -S /tmp/.X11-unix/X99 ]
pgrep -x Xvfb >/dev/null
pgrep -x x11vnc >/dev/null
curl -fsS --max-time 3 -o /dev/null http://127.0.0.1:6080/vnc.html
curl -fsS --max-time 3 -o /dev/null http://127.0.0.1:6090/health
python -c "import camoufox; from rma_poc.doctor import browser_executable; browser_executable()"
