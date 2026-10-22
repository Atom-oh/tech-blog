#!/usr/bin/env bash
# One round of reconcile probes against the hub: Argo CD self-heal, kro propagation, ACK create.
# usage: probe.sh <label>   -> appends one JSON line to results/probes.jsonl
set -uo pipefail; source "$(dirname "$0")/lib.sh"
L=$1; T=$(iso); TIMEOUT=300
# Argo CD self-heal: scale demo-web away from Git (2) and time the revert.
t0=$(now); k -n demo-web scale deploy demo-web --replicas=4 >/dev/null 2>&1; argo=null
while [[ $(el $t0 | cut -d. -f1) -lt $TIMEOUT ]]; do
  r=$(k -n demo-web get deploy demo-web -o jsonpath='{.spec.replicas}' 2>/dev/null)
  [[ $r == 2 ]] && { argo=$(el $t0); break; }; sleep 0.5; done
# kro: flip instance replicas and time the child Deployment update.
cur=$(k -n example get nginxapp reviewed-web -o jsonpath='{.spec.replicas}' 2>/dev/null); want=$(( cur == 2 ? 3 : 2 ))
t0=$(now); k -n example patch nginxapp reviewed-web --type merge -p "{\"spec\":{\"replicas\":$want}}" >/dev/null 2>&1; kro=null
while [[ $(el $t0 | cut -d. -f1) -lt $TIMEOUT ]]; do
  r=$(k -n example get deploy reviewed-web -o jsonpath='{.spec.replicas}' 2>/dev/null)
  [[ $r == "$want" ]] && { kro=$(el $t0); break; }; sleep 0.5; done
# ACK: create a fresh queue and time ResourceSynced=True.
q=upg-$(date -u +%H%M%S); t0=$(now); ack=null
k -n ack-upg apply -f - >/dev/null 2>&1 <<YAML
apiVersion: sqs.services.k8s.aws/v1alpha1
kind: Queue
metadata: { name: $q, namespace: ack-upg }
spec: { queueName: eks-cap-upg-$q }
YAML
while [[ $(el $t0 | cut -d. -f1) -lt $TIMEOUT ]]; do
  s=$(k -n ack-upg get queue $q -o jsonpath='{.status.conditions[?(@.type=="ACK.ResourceSynced")].status}' 2>/dev/null)
  [[ $s == True ]] && { ack=$(el $t0); break; }; sleep 0.5; done
cv=$(aws eks describe-cluster --name $HUB --query 'cluster.[status,version]' --output text | tr '\t' '/')
jq -nc --arg l "$L" --arg t "$T" --arg cv "$cv" --arg q "$q" --argjson a "$argo" --argjson k "$kro" --argjson c "$ack" \
  '{label:$l,start_utc:$t,cluster:$cv,argocd_selfheal_s:$a,kro_propagate_s:$k,ack_queue_synced_s:$c,queue:$q}' | tee -a $RES/probes.jsonl
