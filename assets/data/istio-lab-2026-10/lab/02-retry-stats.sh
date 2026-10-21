#!/usr/bin/env bash
# Expose per-cluster retry counters for the rollout rerun. Istio keeps only a small default stat set, so add
# proxyStatsMatcher for the echo cluster on the sidecar client and on the ambient-l7 waypoint (restarts both).
source "$(dirname "$0")/env.sh"; guard
CFG='{"proxyStatsMatcher":{"inclusionRegexps":[".*echo\\..*\\.upstream_rq_.*",".*echo\\..*\\.upstream_cx_connect_fail"]}}'
kubectl -n sidecar patch deploy client --type merge -p "$(jq -nc --arg c "$CFG" '{spec:{template:{metadata:{annotations:{"proxy.istio.io/config":$c}}}}}')"
kubectl -n sidecar rollout status deploy/client --timeout=180s
kubectl -n ambient-l7 apply -f - <<YAML
apiVersion: v1
kind: ConfigMap
metadata: {name: waypoint-params, namespace: ambient-l7}
data:
  deployment: |
    spec:
      template:
        metadata:
          annotations:
            proxy.istio.io/config: '$CFG'
        spec:
          nodeSelector: {lab-role: server}
YAML
kubectl -n ambient-l7 rollout restart deploy/waypoint
kubectl -n ambient-l7 rollout status deploy/waypoint --timeout=180s
