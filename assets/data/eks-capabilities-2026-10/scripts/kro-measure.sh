#!/usr/bin/env bash
# kro instance lifecycle: create->ACTIVE, spec update->Deployment replicas, delete->children gone.
set -euo pipefail; source "$(dirname "$0")/lib.sh"
L=$1; N=lat-$L
t0=$(now)
kubectl apply -f - >/dev/null <<YAML
apiVersion: platform.example.com/v1alpha1
kind: NginxApp
metadata: { name: $N, namespace: example }
spec: { replicas: 2 }
YAML
until [[ "$(kubectl -n example get nginxapp $N -o jsonpath='{.status.state}' 2>/dev/null)" == ACTIVE ]]; do sleep 0.5; done; t1=$(now)
kubectl -n example patch nginxapp $N --type merge -p '{"spec":{"replicas":3}}' >/dev/null; t2=$(now)
until [[ "$(kubectl -n example get deploy $N -o jsonpath='{.spec.replicas}')" == 3 ]]; do sleep 0.2; done; t3=$(now)
kubectl -n example delete nginxapp $N --wait=false >/dev/null; t4=$(now)
while kubectl -n example get deploy $N >/dev/null 2>&1 || kubectl -n example get svc $N >/dev/null 2>&1; do sleep 0.5; done; t5=$(now)
while kubectl -n example get nginxapp $N >/dev/null 2>&1; do sleep 0.5; done; t6=$(now)
jq -n --arg l $L --argjson c $(echo "$t1-$t0"|bc) --argjson u $(echo "$t3-$t2"|bc) --argjson d $(echo "$t5-$t4"|bc) --argjson f $(echo "$t6-$t4"|bc) \
  '{label:$l,create_to_active_s:$c,spec_update_to_deploy_s:$u,delete_children_gone_s:$d,delete_instance_gone_s:$f}' > $RES/kro-lifecycle-$L.json
cat $RES/kro-lifecycle-$L.json | jq -c .
