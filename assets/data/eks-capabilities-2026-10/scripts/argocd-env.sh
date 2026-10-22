# Source to use the argocd CLI against the managed capability (token from the UI).
export ARGOCD_SERVER=$(aws eks describe-capability --cluster-name eks-cap-demo --capability-name argocd --region ap-northeast-2 --query capability.configuration.argoCd.serverUrl --output text | sed 's|^https://||')
export ARGOCD_AUTH_TOKEN=$(tr -d '\n ' < ~/eks-capabilities-demo/.work/argocd-token) ARGOCD_OPTS=--grpc-web
