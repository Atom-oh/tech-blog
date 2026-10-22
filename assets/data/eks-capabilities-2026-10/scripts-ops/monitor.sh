#!/usr/bin/env bash
# Poll cluster + capability state every 15s until results/STOP exists -> results/monitor.jsonl
set -uo pipefail; source "$(dirname "$0")/lib.sh"
while [[ ! -f $RES/STOP ]]; do
  c=$(aws eks describe-cluster --name $HUB --query 'cluster.[status,version,platformVersion]' --output text | tr '\t' '/')
  caps=$(for n in argocd kro ack; do aws eks describe-capability --cluster-name $HUB --capability-name $n \
    --query 'capability.[capabilityName,status,version,length(health.issues || `[]`)]' --output text | tr '\t' '/'; done | paste -sd' ')
  api=$(k get --raw /version 2>/dev/null | jq -r .gitVersion 2>/dev/null || echo ERR)
  apps=$(k -n argocd get applications -o jsonpath='{range .items[*]}{.metadata.name}={.status.sync.status}/{.status.health.status} {end}' 2>/dev/null || echo ERR)
  jq -nc --arg t "$(iso)" --arg c "$c" --arg caps "$caps" --arg api "$api" --arg apps "$apps" '{t:$t,cluster:$c,caps:$caps,api:$api,apps:$apps}' >> $RES/monitor.jsonl
  sleep 15
done
