#!/usr/bin/env bash
# Render the per-mode namespaces and workloads to stdout. Usage: manifests/workloads.sh | kubectl apply -f -
# Same workload in every namespace; only the namespace labels decide the data plane:
#   base: no mesh, sidecar: istio-injection, ambient-l4: ztunnel only, ambient-l7: ztunnel + waypoint (added by 01-istio.sh).
# echo (3 replicas) runs on the two server nodes, client (fortio load generator) on the client node, so every
# request crosses nodes in the same AZ.
source "$(dirname "$0")/../env.sh"
for ns in "${MODES[@]}"; do
  case $ns in
    base) labels='{}' ;;
    sidecar) labels='{"istio-injection":"enabled"}' ;;
    ambient-l4|ambient-l7) labels='{"istio.io/dataplane-mode":"ambient"}' ;;
  esac
  cat <<EOF
---
apiVersion: v1
kind: Namespace
metadata:
  name: $ns
  labels: $labels
---
apiVersion: v1
kind: Service
metadata: {name: echo, namespace: $ns}
spec:
  selector: {app: echo}
  ports: [{name: http, port: 8080, targetPort: 8080, appProtocol: http}]
---
apiVersion: apps/v1
kind: Deployment
metadata: {name: echo, namespace: $ns}
spec:
  replicas: 3
  selector: {matchLabels: {app: echo}}
  template:
    metadata: {labels: {app: echo, version: v1}}
    spec:
      nodeSelector: {lab-role: server}
      topologySpreadConstraints:
        - {maxSkew: 1, topologyKey: kubernetes.io/hostname, whenUnsatisfiable: DoNotSchedule, labelSelector: {matchLabels: {app: echo}}}
      containers:
        - name: echo
          image: $FORTIO_IMAGE
          args: ["server", "-http-port", "8080"]
          ports: [{containerPort: 8080}]
          readinessProbe: {httpGet: {path: /fortio/, port: 8080}, periodSeconds: 2}
          resources: {requests: {cpu: 200m, memory: 64Mi}, limits: {memory: 256Mi}}
---
apiVersion: apps/v1
kind: Deployment
metadata: {name: client, namespace: $ns}
spec:
  replicas: 1
  selector: {matchLabels: {app: client}}
  template:
    metadata: {labels: {app: client}}
    spec:
      nodeSelector: {lab-role: client}
      containers:
        - name: fortio
          image: $FORTIO_IMAGE
          args: ["server", "-http-port", "8080"]
          resources: {requests: {cpu: 250m, memory: 128Mi}, limits: {memory: 512Mi}}
EOF
done
