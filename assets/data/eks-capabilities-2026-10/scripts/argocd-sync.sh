#!/usr/bin/env bash
# Measure git commit -> app Synced+Healthy with new content, and drift self-heal.
# Usage: argocd-sync.sh <label> <commit|drift> ; requires app demo-web already Synced.
set -euo pipefail; source "$(dirname "$0")/lib.sh"
L=$1; MODE=$2; REPO=~/eks-capabilities-demo/repo
case $MODE in
commit)
  cd $REPO; cur=$(grep -o 'v[0-9]*' apps/demo-web/configmap.yaml | tail -1); nxt="v$(( ${cur#v} + 1 ))"
  sed -i "s/version=$cur/version=$nxt/" apps/demo-web/configmap.yaml
  git commit -qam "demo-web $nxt" && t0=$(now); git push -q; sha=$(git rev-parse HEAD)
  log "pushed $sha ($nxt)"
  while :; do
    r=$(kubectl -n argocd get application demo-web -o jsonpath='{.status.sync.revision} {.status.sync.status}')
    [[ $r == "$sha Synced" ]] && break; sleep 2
  done; t1=$(now)
  until [[ "$(kubectl -n demo-web get cm demo-web-content -o jsonpath='{.data.index\.html}')" == *"$nxt"* ]]; do sleep 1; done; t2=$(now)
  jq -n --arg l $L --arg v $nxt --argjson synced $(echo "$t1-$t0"|bc) --argjson applied $(echo "$t2-$t0"|bc) \
    '{label:$l,version:$v,push_to_synced_s:$synced,push_to_configmap_s:$applied}' | tee $RES/argocd-commit-$L.json ;;
drift)
  t0=$(now); kubectl -n demo-web scale deploy demo-web --replicas=5 >/dev/null; log "scaled to 5 (drift)"
  until [[ "$(kubectl -n demo-web get deploy demo-web -o jsonpath='{.spec.replicas}')" == 2 ]]; do sleep 0.5; done; t1=$(now)
  jq -n --arg l $L --argjson s $(echo "$t1-$t0"|bc) '{label:$l,drift_to_reverted_s:$s}' | tee $RES/argocd-drift-$L.json ;;
esac
