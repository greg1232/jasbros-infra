#! /bin/bash

hits=$(for net in 4 5; do
  for i in $(seq 1 254); do
    (timeout 1 bash -c "echo >/dev/tcp/192.168.$net.$i/22" 2>/dev/null && echo "192.168.$net.$i") &
  done; wait
done 2>/dev/null | sort -V | grep -vE "192\.168\.4\.(95|97)$")
if [ -n "$hits" ]; then echo "[$(date +%H:%M:%S)] NEW HOST: $hits"; exit 0; fi
echo "[$(date +%H:%M:%S)] round $round: nothing new"
