#!/usr/bin/env bash
# DripVault persistent public tunnel via pinggy (URL printed to stdout → log file).
# Usage: bash scripts/pinggy-tunnel.sh   (runs until killed; URL in pinggy-url.log)
ssh -o StrictHostKeyChecking=no -o ServerAliveInterval=30 -p 443 \
    -N -R 0:127.0.0.1:8787 free.pinggy.io > /home/ubuntu/dripvault/pinggy-url.log 2>&1
