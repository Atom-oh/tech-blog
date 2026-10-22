# Shared helpers for capability measurements.
export AWS_REGION=ap-northeast-2 CLUSTER=eks-cap-demo
export ACCT=$(aws sts get-caller-identity --query Account --output text)
export CLUSTER_ARN=arn:aws:eks:$AWS_REGION:$ACCT:cluster/$CLUSTER
RES=~/eks-capabilities-demo/.work/results
now() { date -u +%s.%N | cut -c1-14; }
iso() { date -u +%H:%M:%S; }
log() { echo "[$(iso)] $*"; }
# wait_capability <name> <target-status>: prints seconds waited
wait_capability() {
  local name=$1 target=$2 t0=$(now) s
  while :; do
    s=$(aws eks describe-capability --cluster-name $CLUSTER --capability-name $name --query capability.status --output text 2>/dev/null || echo NOTFOUND)
    [[ $s == "$target" ]] && break
    [[ $s == *FAILED ]] && { log "status=$s"; return 1; }
    sleep 5
  done
  echo "$(echo "$(now) - $t0" | bc)"
}
# wait_gone <name>: poll until genuine not-found
wait_capability_gone() {
  local name=$1 t0=$(now)
  while aws eks describe-capability --cluster-name $CLUSTER --capability-name $name >/dev/null 2>&1; do sleep 5; done
  echo "$(echo "$(now) - $t0" | bc)"
}
