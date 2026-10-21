# Shared settings for the Istio lab. Source this from every script.
set -euo pipefail
export AWS_REGION=ap-northeast-2
export CLUSTER=istio-lab
export ISTIO_VERSION=1.31.2
export ISTIOCTL="$HOME/.local/istio-${ISTIO_VERSION}/istioctl"
export GATEWAY_API_VERSION=v1.6.0
export FORTIO_IMAGE="fortio/fortio:1.76.0@sha256:3dd31ddeb4f962a1dae8cc2a0bce62c408ac830c8435bbea51b1e0fd4c8f7181"
export LAB="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export RESULTS="$LAB/results"
# Isolated kubeconfig: never touch ~/.kube/config.
export KUBECONFIG="$HOME/.kube/${CLUSTER}.yaml"
MODES=(base sidecar ambient-l4 ambient-l7)
log() { printf '\033[1;34m[%s]\033[0m %s\n' "$(date +%H:%M:%S)" "$*" >&2; }
guard() {
  [[ "$(aws sts get-caller-identity --query Account --output text)" == "${EXPECTED_ACCOUNT:?set EXPECTED_ACCOUNT}" ]] || { echo "wrong AWS account"; exit 1; }
  kubectl config current-context 2>/dev/null | grep -q "$CLUSTER" || { echo "WRONG CONTEXT"; exit 1; }
}
