#!/usr/bin/env bash
# DripVault public URL via serveo tunnel (persistent). Restart = URL baru.
pkill -f "ssh.*serveo.net" 2>/dev/null
sleep 2
ssh -o StrictHostKeyChecking=no -o ServerAliveInterval=30 -o ExitOnForwardFailure=yes \
    -N -R 80:127.0.0.1:8787 serveo.net > /home/ubuntu/dripvault/serveo-url.log 2>&1
