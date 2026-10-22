# Shared helpers for the upgrade / multi-cluster lab.
export AWS_REGION=ap-northeast-2 HUB=eks-cap-hub SPOKE=eks-cap-spoke
export ACCT=$(aws sts get-caller-identity --query Account --output text)
export KCTX=$(kubectl config get-contexts -o name | grep "@$HUB\." | head -1)
RES=~/eks-capabilities-demo/upgrade/results
now() { date -u +%s.%N | cut -c1-14; }
iso() { date -u +%H:%M:%S; }
log() { echo "[$(iso)] $*"; }
k() { kubectl --context "$KCTX" --request-timeout=10s "$@"; }
el() { echo "$(now) - $1" | bc; }
wait_cap() { # wait_cap <name> <status>
  local t0=$(now) s
  while :; do
    s=$(aws eks describe-capability --cluster-name $HUB --capability-name $1 --query capability.status --output text 2>/dev/null || echo NOTFOUND)
    [[ $s == "$2" ]] && break; [[ $s == *FAILED ]] && { log "$1 $s"; return 1; }; sleep 5
  done; el $t0
}
