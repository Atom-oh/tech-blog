#!/usr/bin/env bash
# Create the managed ACK capability (role can only assume team roles) and time it.
set -euo pipefail; source "$(dirname "$0")/lib.sh"
RUN=${1:-run1}; t0=$(iso)
aws eks create-capability --cluster-name $CLUSTER --capability-name ack --type ACK \
  --role-arn arn:aws:iam::$ACCT:role/eks-cap-demo-ack --delete-propagation-policy RETAIN \
  --query 'capability.[capabilityName,status]' --output text
secs=$(wait_capability ack ACTIVE); log "ack ACTIVE after ${secs}s (started $t0)"
aws eks describe-capability --cluster-name $CLUSTER --capability-name ack > $RES/ack-describe-$RUN.json
jq -n --arg run $RUN --arg start $t0 --argjson secs $secs '{run:$run,start_utc:$start,seconds_to_active:$secs}' > $RES/ack-create-$RUN.json
