#!/bin/sh
# One-shot Garage bootstrap: single-node layout, bucket, deterministic S3 key.
# Idempotent: safe to re-run. Real errors fail loudly (non-zero exit).
set -eu

: "${GARAGE_RPC_SECRET:?GARAGE_RPC_SECRET required}"
: "${S3_ACCESS_KEY:?S3_ACCESS_KEY required}"
: "${S3_SECRET_KEY:?S3_SECRET_KEY required}"
: "${S3_BUCKET:=reelforge}"

export GARAGE_RPC_SECRET

echo "[garage-init] waiting for server..."
for _ in $(seq 1 60); do
  if garage status >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

NODE_ID=$(garage status 2>&1 | awk '$1 ~ /^[0-9a-f]+$/ && length($1) >= 8 {print $1; exit}')
if [ -z "$NODE_ID" ]; then
  echo "[garage-init] ERROR: could not parse node id from garage status" >&2
  exit 1
fi
echo "[garage-init] node id: $NODE_ID"

if garage layout show 2>&1 | grep -q "NO ROLE ASSIGNED"; then
  garage layout assign -z dc1 -c 10G "$NODE_ID"
  garage layout apply --version 1
else
  echo "[garage-init] layout already assigned, skipping"
fi

if garage bucket info "$S3_BUCKET" >/dev/null 2>&1; then
  echo "[garage-init] bucket $S3_BUCKET exists, skipping create"
else
  garage bucket create "$S3_BUCKET"
fi

if garage key info "$S3_ACCESS_KEY" >/dev/null 2>&1; then
  echo "[garage-init] key $S3_ACCESS_KEY exists, skipping import"
else
  garage key import --yes -n reelforge "$S3_ACCESS_KEY" "$S3_SECRET_KEY"
fi

garage bucket allow --read --write --owner "$S3_BUCKET" --key "$S3_ACCESS_KEY"

echo "[garage-init] done:"
garage bucket info "$S3_BUCKET"
