#!/usr/bin/env bash
# Create the managed kro capability (role without policies) and time it.
set -euo pipefail; source "$(dirname "$0")/lib.sh"
RUN=${1:-run1}; t0=$(iso)
aws eks create-capability --cluster-name $CLUSTER --capability-name kro --type KRO \
  --role-arn arn:aws:iam::$ACCT:role/eks-cap-demo-kro --delete-propagation-policy RETAIN \
  --query 'capability.[capabilityName,status]' --output text
secs=$(wait_capability kro ACTIVE); log "kro ACTIVE after ${secs}s (started $t0)"
aws eks describe-capability --cluster-name $CLUSTER --capability-name kro > $RES/kro-describe-$RUN.json
jq -n --arg run $RUN --arg start $t0 --argjson secs $secs '{run:$run,start_utc:$start,seconds_to_active:$secs}' > $RES/kro-create-$RUN.json
