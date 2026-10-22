#!/usr/bin/env bash
# Create a fresh Bucket+Queue+Table in team-a and a denied Bucket in team-b; time ResourceSynced.
set -euo pipefail; source "$(dirname "$0")/lib.sh"
L=$1; S=$(cat ~/eks-capabilities-demo/.work/ack-suffix); P=m$(date +%H%M%S)
kubectl apply -f - >/dev/null <<YAML
apiVersion: s3.services.k8s.aws/v1alpha1
kind: Bucket
metadata: { name: $P, namespace: team-a }
spec: { name: eks-cap-demo-$S-$P }
---
apiVersion: sqs.services.k8s.aws/v1alpha1
kind: Queue
metadata: { name: $P, namespace: team-a }
spec: { queueName: eks-cap-demo-$S-$P }
---
apiVersion: dynamodb.services.k8s.aws/v1alpha1
kind: Table
metadata: { name: $P, namespace: team-a }
spec:
  tableName: eks-cap-demo-$S-$P
  billingMode: PAY_PER_REQUEST
  attributeDefinitions: [{ attributeName: id, attributeType: S }]
  keySchema: [{ attributeName: id, keyType: HASH }]
---
apiVersion: s3.services.k8s.aws/v1alpha1
kind: Bucket
metadata: { name: $P-denied, namespace: team-b }
spec: { name: eks-cap-demo-$S-$P-denied }
YAML
t0=$(now); declare -A d
for i in $(seq 1 180); do
  for k in bucket.s3 queue.sqs table.dynamodb; do [[ -n ${d[$k]:-} ]] && continue
    [[ "$(kubectl -n team-a get $k.services.k8s.aws $P -o jsonpath='{.status.conditions[?(@.type=="ACK.ResourceSynced")].status}')" == True ]] && d[$k]=$(echo "$(now)-$t0"|bc); done
  [[ ${#d[@]} == 3 ]] && break; sleep 1; done
deny=$(kubectl -n team-b get bucket.s3.services.k8s.aws $P-denied -o jsonpath='{.status.conditions[?(@.type=="Ready")].message}' | grep -o 'AccessDenied' | head -1 || true)
jq -n --arg l $L --arg p $P --argjson b ${d[bucket.s3]:-null} --argjson q ${d[queue.sqs]:-null} --argjson t ${d[table.dynamodb]:-null} --arg deny "$deny" \
  '{label:$l,prefix:$p,bucket_s:$b,queue_s:$q,table_s:$t,team_b_bucket:$deny}' | tee $RES/ack-resources-$L.json | jq -c .
