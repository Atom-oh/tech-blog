#!/usr/bin/env bash
# Create the managed Argo CD capability and record time-to-ACTIVE.
set -euo pipefail; source "$(dirname "$0")/lib.sh"
RUN=${1:-run1}
IDC_ARN=$(aws sso-admin list-instances --query 'Instances[0].InstanceArn' --output text)
IDC_USER=$(aws identitystore list-users --identity-store-id $(aws sso-admin list-instances --query 'Instances[0].IdentityStoreId' --output text) \
  --query "Users[?UserName=='${IDC_USERNAME:?set IDC_USERNAME}'].UserId | [0]" --output text)
t0=$(iso)
aws eks create-capability --cluster-name $CLUSTER --capability-name argocd --type ARGOCD \
  --role-arn arn:aws:iam::$ACCT:role/eks-cap-demo-argocd --delete-propagation-policy RETAIN \
  --configuration "{\"argoCd\":{\"namespace\":\"argocd\",\"awsIdc\":{\"idcInstanceArn\":\"$IDC_ARN\",\"idcRegion\":\"$AWS_REGION\"},
    \"rbacRoleMappings\":[{\"role\":\"ADMIN\",\"identities\":[{\"id\":\"$IDC_USER\",\"type\":\"SSO_USER\"}]}]}}" \
  --query 'capability.[capabilityName,status]' --output text
secs=$(wait_capability argocd ACTIVE)
log "argocd ACTIVE after ${secs}s (started $t0)"
aws eks describe-capability --cluster-name $CLUSTER --capability-name argocd > $RES/argocd-describe-$RUN.json
jq -n --arg run $RUN --arg start $t0 --argjson secs $secs '{run:$run,start_utc:$start,seconds_to_active:$secs}' > $RES/argocd-create-$RUN.json
