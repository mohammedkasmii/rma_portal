#!/usr/bin/env bash
# Open the visible OmegaFlow login in the noVNC desktop and capture the session.
#
#   scripts/prod/connect-session.sh
#
# 1. run this script; 2. open the noVNC URL it prints and enter the VNC password;
# 3. log in to OmegaFlow by hand (including any session-validation screen). The command
# waits for positive authentication, saves the session atomically and exits with
# RESULT=CAPTURED. The worker picks the session up on its next cycle.
# If the worker is mid-cycle the profile is busy (RESULT=PROFILE_BUSY): retry in a minute.
set -euo pipefail
# shellcheck source=lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

require_stack_config
echo "noVNC desktop: $(env_value RMA_NOVNC_URL "http://<serveur>:$(env_value RMA_NOVNC_PORT 6081)/vnc.html")"
echo "Connectez-vous a OmegaFlow dans cette fenetre ; ce script se termine des que la session est capturee."
compose exec browser python -m rma_poc login --timeout "${LOGIN_TIMEOUT:-900}"
