#!/usr/bin/env bash
# Render eksctl config for eks-cap-demo from live demo-vpc lookups (no hardcoded IDs).
set -euo pipefail
REGION=ap-northeast-2
VPC_ID=$(aws ec2 describe-vpcs --region $REGION --filters Name=tag:Name,Values=fsi-demo-vpc --query 'Vpcs[0].VpcId' --output text)
sub() { aws ec2 describe-subnets --region $REGION --filters Name=tag:Name,Values="FsiDemoNetworkStack/FsiDemoVpc/$1" --query 'Subnets[0].SubnetId' --output text; }
PRIV1=$(sub PrivateSubnet1); PRIV2=$(sub PrivateSubnet2)
EGRESS_IP=$(curl -s https://checkip.amazonaws.com)
cat <<YAML
apiVersion: eksctl.io/v1alpha5
kind: ClusterConfig
metadata:
  name: eks-cap-demo
  region: $REGION
  version: "1.36"
  tags:
    Project: eks-capabilities-blog
accessConfig:
  authenticationMode: API
vpc:
  id: $VPC_ID
  subnets:
    private:
      ap-northeast-2a: { id: $PRIV1 }
      ap-northeast-2b: { id: $PRIV2 }
  clusterEndpoints:
    publicAccess: true
    privateAccess: true
  publicAccessCIDRs: ["$EGRESS_IP/32"]
iam:
  withOIDC: false
addons:
  - name: vpc-cni
  - name: coredns
  - name: kube-proxy
  - name: eks-pod-identity-agent
  - name: metrics-server
managedNodeGroups:
  - name: ng-arm
    instanceType: m7g.large
    desiredCapacity: 2
    minSize: 2
    maxSize: 2
    privateNetworking: true
    volumeSize: 30
YAML
