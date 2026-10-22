#!/usr/bin/env bash
# Render eksctl config for the upgrade/multi-cluster lab from live demo-vpc lookups.
# usage: render.sh <name> <version>
set -euo pipefail
NAME=$1 VER=$2 REGION=ap-northeast-2
VPC_ID=$(aws ec2 describe-vpcs --region $REGION --filters Name=tag:Name,Values=fsi-demo-vpc --query 'Vpcs[0].VpcId' --output text)
sub() { aws ec2 describe-subnets --region $REGION --filters Name=tag:Name,Values="FsiDemoNetworkStack/FsiDemoVpc/$1" --query 'Subnets[0].SubnetId' --output text; }
PRIV1=$(sub PrivateSubnet1); PRIV2=$(sub PrivateSubnet2)
EGRESS_IP=$(curl -s https://checkip.amazonaws.com)
cat <<YAML
apiVersion: eksctl.io/v1alpha5
kind: ClusterConfig
metadata:
  name: $NAME
  region: $REGION
  version: "$VER"
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
managedNodeGroups:
  - name: ng-arm
    instanceType: m7g.large
    desiredCapacity: 1
    minSize: 1
    maxSize: 1
    privateNetworking: true
    volumeSize: 30
YAML
