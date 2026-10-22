#!/usr/bin/env bash
# Grant capability permissions on the hub and deploy the baseline workloads for the upgrade test.
set -euo pipefail; source "$(dirname "$0")/lib.sh"
R_ARGO=arn:aws:iam::$ACCT:role/eks-cap-hub-argocd R_KRO=arn:aws:iam::$ACCT:role/eks-cap-hub-kro
for p in AmazonEKSEditPolicy AmazonEKSAdminViewPolicy; do
  aws eks associate-access-policy --cluster-name $HUB --principal-arn $R_ARGO \
    --policy-arn arn:aws:eks::aws:cluster-access-policy/$p --access-scope type=cluster --query associatedAccessPolicy.policyArn --output text
done
aws eks update-access-entry --cluster-name $HUB --principal-arn $R_KRO --kubernetes-groups kro-workloads --query accessEntry.kubernetesGroups --output text
REPO=~/eks-capabilities-demo/repo
k apply -f $REPO/kro/nginxapp-rbac.yaml
k apply -f - <<YAML
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata: { name: kro-workloads-reviewed-nginxapps }
roleRef: { apiGroup: rbac.authorization.k8s.io, kind: ClusterRole, name: "kro:controller:reviewed-nginxapps" }
subjects: [{ kind: Group, name: kro-workloads, apiGroup: rbac.authorization.k8s.io }]
YAML
k apply -f $REPO/kro/nginxapp-rgd.yaml
k create ns example --dry-run=client -o yaml | k apply -f -
k create ns ack-upg --dry-run=client -o yaml | k apply -f -
k -n argocd apply -f - <<YAML
apiVersion: v1
kind: Secret
metadata: { name: eks-cap-hub, namespace: argocd, labels: { argocd.argoproj.io/secret-type: cluster, role: hub } }
stringData: { name: eks-cap-hub, server: "arn:aws:eks:$AWS_REGION:$ACCT:cluster/$HUB", project: default }
---
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata: { name: demo-web-hub, namespace: argocd }
spec:
  project: default
  source: { repoURL: https://github.com/Atom-oh/eks-capabilities-demo.git, targetRevision: main, path: apps/demo-web }
  destination: { name: eks-cap-hub, namespace: demo-web }
  syncPolicy: { automated: { prune: true, selfHeal: true }, syncOptions: [CreateNamespace=true] }
YAML
