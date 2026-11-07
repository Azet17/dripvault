#!/usr/bin/env bash
# DripVault public URL via localhost.run tunnel (persistent). Restart = URL baru.
pkill -f "ssh.*localhost.run" 2>/dev/null
sleep 2
ssh -o StrictHostKeyChecking=no -o ServerAliveInterval=30 -o ExitOnForwardFailure=yes \
    -N -R 80:127.0.0.1:8787 nokey@localhost.run > /home/ubuntu/dripvault/lhr-url.log 2>&1
