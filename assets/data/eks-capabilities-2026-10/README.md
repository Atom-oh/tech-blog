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

## 추가 측정: 클러스터 업그레이드와 멀티 클러스터 (2026-10-22)

환경: hub `eks-cap-hub`(EKS 1.36 → 1.37, m7g.large 1대)에 관리형 Argo CD `3.4.7-eks-1`, kro `0.9.4-eks-1`, ACK `46.186.1-eks-1`. spoke `eks-cap-spoke`(EKS 1.37, 엔드포인트 private 전용, m7g.large 1대).

| 파일 | 내용 |
|---|---|
| `results-ops/probes.jsonl` | 한 줄이 측정 1회. Argo CD self-heal(replicas 2→4 후 2로 돌아오기까지), kro 인스턴스 replicas 변경이 하위 Deployment에 반영되기까지, 새 SQS Queue CR의 `ACK.ResourceSynced=True`까지(초). `before-*` 업그레이드 전, `during-*` 업그레이드 중, `control-*` 업그레이드 없이 같은 간격, `after-cooldown` 6분 쉰 뒤. `cluster`는 측정이 끝난 시점의 상태 |
| `results-ops/monitor.jsonl` | 약 19초마다(15초 대기 + 조회 시간) 기록한 클러스터 상태와 버전, capability 상태와 버전, API 서버 `/version`, Application 상태 |
| `results-ops/describe-*-{before,after}.json` | 업그레이드 전후 `describe-capability` 요약 |
| `results-ops/insights-*.txt` | 업그레이드 전후 EKS upgrade insights |
| `results-ops/upgrade-start.txt`, `upgrade-end.txt` | 업그레이드 요청 시각, 측정 루프가 `describe-update` 성공을 확인한 시각(측정 1회가 끝난 뒤 확인하므로 실제 완료보다 늦음. 클러스터가 `ACTIVE`가 된 시각은 `monitor.jsonl` 기준 02:55:52) |
| `results-ops/argocd-app-after-upgrade.json` | 업그레이드 측정 직후 Application 상태(`autoHealAttemptsCount: 7`) |
| `results-ops/spoke-register.json` | private spoke 등록 결과와 권한 오류. `registered_utc`, `still_failing_after_s`, `failure_message`, `hub_spoke_network`는 측정 당시 터미널 출력(등록 시각, 303.7초 대기 후 Application 상태와 operationState 메시지)에서 나중에 옮겨 적은 값 |
| `results-ops/endpoint-prefix-reuse.json` | `endpointPrefix`를 지정한 Argo CD capability를 지우고 같은 이름으로 다시 만들었을 때의 주소와 소요 시간 |

시각은 모두 UTC입니다. `probe.sh`는 Argo CD, kro, ACK 순서로 재므로, Argo CD self-heal이 늦어지면 kro와 ACK 측정도 그만큼 늦게 시작합니다.

`scripts-ops/`는 이 측정에 쓴 스크립트입니다(`render.sh`, `setup-hub.sh`, `probe.sh`, `monitor.sh`, `lib.sh`).
