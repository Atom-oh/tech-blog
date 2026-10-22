# EKS Capabilities(Argo CD, kro, ACK) 측정 원시 결과 (2026-10-21)

글: https://blog.atomai.click/eks-capabilities-argocd/ , https://blog.atomai.click/eks-capabilities-kro/ , https://blog.atomai.click/eks-capabilities-ack/

환경: EKS 1.36.4(ap-northeast-2, 기존 VPC의 private 서브넷), m7g.large 2대. 관리형 Argo CD `3.4.6-eks-2`, kro `0.9.4-eks-1`, ACK `46.186.1-eks-1`. 비교용 self-managed Argo CD 3.4.6(Helm 10.2.2), kro 0.9.4, ACK s3 1.13.1, sqs 1.8.2, dynamodb 1.13.1.
매니페스트는 https://github.com/Atom-oh/eks-capabilities-demo 에 있습니다.

| 파일 | 내용 |
|---|---|
| `results/*-create-run*.json` | capability 생성 요청부터 `ACTIVE`까지 걸린 시간 |
| `results/*-describe-run*.json` | 생성 직후 `describe-capability` 출력(버전, 설정) |
| `results/argocd-commit-*.json` | git push부터 Application sync revision이 새 commit이 되기까지(`push_to_synced_s`), 대상 ConfigMap 반영까지(`push_to_configmap_s`). `managed-poll`, `managed-webhook`, `oss-poll` |
| `results/argocd-drift-*.json` | Deployment replicas를 5로 바꾼 뒤 selfHeal로 2가 되기까지 |
| `results/kro-lifecycle-*.json` | NginxApp 인스턴스 생성→ACTIVE, spec 변경→Deployment 반영, 삭제→하위 리소스와 인스턴스 소멸 |
| `results/kro-ack-orderservice-*.json` | kro+ACK OrderService: 큐 URL과 테이블 ARN이 status에 나타나기까지, 인스턴스 ACTIVE까지 |
| `results/kro-ack-name-collision.md` | 같은 이름의 기존 ACK CR을 kro가 덮어쓴 과정(관리형과 OSS 모두) |
| `results/ack-create-resources-managed-1.json` | capability 생성 직후 첫 리소스 동기화(콜드 스타트, 비교표에서 제외) |
| `results/ack-resources-*.json` | team-a Bucket, Queue, Table의 `ACK.ResourceSynced=True`까지 시간, team-b Bucket 거부 여부 |
| `results/ack-drift-*.txt` | ACK 밖에서 SQS `VisibilityTimeout`을 바꾼 뒤 복구까지(최대 1,500초) |

`scripts/`는 측정에 쓴 스크립트입니다. `render-cluster.sh`는 기존 VPC를 이름 태그로 찾아 eksctl 설정을 만듭니다. VPC 이름, 클러스터 이름, 리전은 환경에 맞게 바꿔야 합니다. `argocd-create.sh`는 `IDC_USERNAME`(Identity Center 사용자 이름)을 받아 ADMIN으로 매핑합니다. `argocd-env.sh`는 UI에서 만든 토큰 파일을 읽어 argocd CLI 환경 변수를 설정합니다.

결과 파일의 계정 ID, Identity Center 인스턴스 ID, UUID는 자리표시자로 바꿨습니다.
