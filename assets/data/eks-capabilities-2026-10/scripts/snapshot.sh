#!/usr/bin/env bash
# Snapshot cluster footprint: pods, top usage, CRDs, access entries. Usage: snapshot.sh <label>
set -uo pipefail; source "$(dirname "$0")/lib.sh"
L=$1; D=$RES/snap-$L; mkdir -p $D
kubectl get pods -A -o wide > $D/pods.txt
kubectl top nodes > $D/top-nodes.txt 2>&1
kubectl top pods -A --sum > $D/top-pods.txt 2>&1
kubectl get crd -o name | sort > $D/crds.txt
aws eks list-access-entries --cluster-name $CLUSTER --output json > $D/access-entries.json
for p in $(jq -r '.accessEntries[]' $D/access-entries.json); do
  aws eks list-associated-access-policies --cluster-name $CLUSTER --principal-arn $p --output json | jq --arg p $p '{principal:$p,policies:[.associatedAccessPolicies[]|{policyArn,accessScope}]}'
done > $D/access-policies.json
echo "$L: pods=$(($(wc -l < $D/pods.txt)-1)) crds=$(wc -l < $D/crds.txt) entries=$(jq '.accessEntries|length' $D/access-entries.json)"
