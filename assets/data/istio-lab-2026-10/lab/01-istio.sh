#!/usr/bin/env bash
# Install Gateway API CRDs + Istio (ambient profile, which also serves sidecar injection), enable Envoy access logs,
# deploy the workloads and the ambient-l7 waypoint, then STRICT mTLS in the three mesh namespaces.
# Usage: ./01-istio.sh            (idempotent)
source "$(dirname "$0")/env.sh"; guard

log "Gateway API CRDs $GATEWAY_API_VERSION (standard channel)"
kubectl apply --server-side -f "https://github.com/kubernetes-sigs/gateway-api/releases/download/${GATEWAY_API_VERSION}/standard-install.yaml" >/dev/null

log "Istio $ISTIO_VERSION, ambient profile; istiod pinned to the client node so it never shares a server node with echo"
"$ISTIOCTL" install -y --set profile=ambient \
  --set meshConfig.accessLogFile=/dev/stdout \
  --set 'components.pilot.k8s.nodeSelector.lab-role=client' >/dev/null
kubectl -n istio-system rollout status deploy/istiod --timeout=180s >/dev/null
kubectl -n istio-system rollout status ds/ztunnel --timeout=180s >/dev/null

log "workloads in ${MODES[*]}"
"$LAB/manifests/workloads.sh" | kubectl apply -f - >/dev/null

log "waypoint for ambient-l7 (namespace enrolled), pinned to server nodes"
# Same shape as `istioctl waypoint apply`, plus infrastructure.parametersRef so istiod renders the nodeSelector
# (a kubectl patch on the generated Deployment would be reconciled away).
kubectl apply -f - >/dev/null <<'EOF'
apiVersion: v1
kind: ConfigMap
metadata: {name: waypoint-params, namespace: ambient-l7}
data:
  deployment: |
    spec:
      template:
        spec:
          nodeSelector: {lab-role: server}
---
apiVersion: gateway.networking.k8s.io/v1
kind: Gateway
metadata:
  name: waypoint
  namespace: ambient-l7
  labels: {istio.io/waypoint-for: service}
spec:
  gatewayClassName: istio-waypoint
  infrastructure:
    parametersRef: {group: "", kind: ConfigMap, name: waypoint-params}
  listeners: [{name: mesh, port: 15008, protocol: HBONE}]
EOF
kubectl label ns ambient-l7 istio.io/use-waypoint=waypoint --overwrite >/dev/null
kubectl -n ambient-l7 wait --for=condition=Programmed gateway/waypoint --timeout=120s >/dev/null
kubectl -n ambient-l7 rollout status deploy/waypoint --timeout=120s >/dev/null

for ns in "${MODES[@]}"; do kubectl -n "$ns" rollout status deploy/echo deploy/client --timeout=180s >/dev/null; done

log "STRICT mTLS in the mesh namespaces"
for ns in sidecar ambient-l4 ambient-l7; do
  kubectl apply -f - >/dev/null <<EOF
apiVersion: security.istio.io/v1
kind: PeerAuthentication
metadata: {name: default, namespace: $ns}
spec: {mtls: {mode: STRICT}}
EOF
done

log "placement"
kubectl get pods -A -o wide --no-headers | awk '$1 ~ /^(base|sidecar|ambient-l4|ambient-l7|istio-system)$/ {print $1, $2, $3, $8}' | column -t >&2
