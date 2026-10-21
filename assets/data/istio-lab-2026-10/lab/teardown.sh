#!/usr/bin/env bash
# Delete everything the lab created: the istio-lab cluster (eksctl stacks) only. The VPC is shared and stays.
source "$(dirname "$0")/env.sh"
# Deletion must validate the account, even when a partial cluster has no kube context.
guard_account
log "deleting cluster $CLUSTER (waits for the CloudFormation stacks)"
eksctl delete cluster --name "$CLUSTER" --region "$AWS_REGION" --wait --disable-nodegroup-eviction
log "left in the account (should be empty):"
aws cloudformation list-stacks --region "$AWS_REGION" --query "StackSummaries[?starts_with(StackName,'eksctl-${CLUSTER}') && StackStatus!='DELETE_COMPLETE'].[StackName,StackStatus]" --output text
aws ec2 describe-instances --region "$AWS_REGION" --filters "Name=tag:eks:cluster-name,Values=$CLUSTER" "Name=instance-state-name,Values=pending,running,stopping,stopped" --query 'Reservations[].Instances[].InstanceId' --output text
aws ec2 describe-network-interfaces --region "$AWS_REGION" --filters "Name=tag:cluster.k8s.amazonaws.com/name,Values=$CLUSTER" --query 'NetworkInterfaces[].NetworkInterfaceId' --output text
