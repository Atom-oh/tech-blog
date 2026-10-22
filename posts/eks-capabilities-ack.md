---
title: "EKS의 관리형 ACK를 직접 써 봤습니다: CRD 267개를 Pod 없이, 네임스페이스별 IAM 분리, 25분 안에 복구되지 않은 drift와 OSS 비교"
date: 2026-10-22
description: "EKS Capabilities의 ACK(AWS Controllers for Kubernetes)를 EKS 1.36 클러스터에서 생성하고, IAMRoleSelector로 팀별 IAM 권한을 나누고, 리소스 생성, drift 복구, adoption, retain 삭제를 측정했습니다. 같은 시나리오를 self-managed ACK 컨트롤러 3개로 반복해 운영 부담, 기본값, 기능 차이를 비교했습니다."
tags: eks, eks-capabilities, ack, platform-engineering
---

# EKS의 관리형 ACK를 직접 써 봤습니다: CRD 267개를 Pod 없이, 네임스페이스별 IAM 분리, 25분 안에 복구되지 않은 drift와 OSS 비교

<p class="series">EKS Capabilities 직접 써 보기: <a href="/eks-capabilities-argocd/">1편 Argo CD</a> · <a href="/eks-capabilities-kro/">2편 kro</a> · <strong>3편 ACK</strong></p>

<p class="lead">ACK(AWS Controllers for Kubernetes)는 S3 버킷이나 SQS 큐 같은 AWS 리소스를 Kubernetes 리소스로 선언하게 해 줍니다. self-managed로 쓰면 서비스마다 컨트롤러를 하나씩 설치하고, 서비스마다 IAM 권한을 연결해야 합니다. EKS Capabilities의 ACK는 이 컨트롤러들을 AWS가 클러스터 밖에서 운영합니다. 이번 편에서는 "컨트롤러 수십 개를 한 번에 맡기면 무엇이 편해지고, 팀별 권한은 어떻게 나누며, OSS와 비교해 무엇을 잃는가"를 확인했습니다. 시리즈 마지막 편이라 Argo CD, kro와 함께 묶은 구성도 다룹니다. 측정은 2026년 10월 21일에 했습니다.</p>

<div class="callout"><strong>시험 환경 요약</strong>EKS 1.36.4(ap-northeast-2, 기존 VPC의 private 서브넷), 관리형 노드 그룹 m7g.large 2대, 관리형 ACK <code>46.186.1-eks-1</code>(s3 컨트롤러 v1.12.2, sqs v1.7.1), 비교용 self-managed ACK s3 1.13.1, sqs 1.8.2, dynamodb 1.13.1(Helm, EKS Pod Identity). 리소스는 S3 Bucket, SQS Queue, DynamoDB Table(온디맨드)이고, team-a와 team-b 두 네임스페이스에 서로 다른 IAM 역할을 연결했습니다. 시나리오마다 2~3회만 반복했고, drift 복구는 관리형 2회, OSS 1회만 측정했습니다.</div>

<div class="callout warn"><strong>먼저 결론</strong>
1) 처음 만들 때 386초가 걸렸고, CRD 267개(서비스 API 그룹 66개, 핵심 그룹에는 <code>IAMRoleSelector</code> 하나)가 설치됐습니다. 워커 노드에 컨트롤러 Pod는 하나도 없습니다. CRD가 이미 있는 상태에서 다시 만들면 77초였습니다.<br>
2) IAMRoleSelector로 네임스페이스마다 다른 IAM 역할을 쓰게 할 수 있었습니다. capability role에는 AssumeRole 권한만 두었고, SQS 권한만 받은 team-b에서 만든 S3 Bucket은 <code>AccessDenied</code>로 막혔습니다. 조건에는 <code>ACK.IAMRoleSelected=True</code>가 남아 어떤 역할이 쓰였는지 알 수 있습니다.<br>
3) 리소스 생성 시간은 관리형과 OSS 사이에 차이가 없었습니다(두 쪽 합쳐 Queue 1.0~1.2초, Bucket 3.1~3.3초, Table 10.2~11.5초). 관리형은 AWS 리소스에 <code>eks:eks-capability-arn</code>, <code>eks:kubernetes-namespace</code> 등 태그 5개를 붙이고, OSS는 <code>services.k8s.aws/*</code> 태그 2개를 붙입니다.<br>
4) ACK 밖에서 바꾼 SQS 속성을 관리형과 OSS 모두 주기적으로 되돌린 경우는 없었습니다(최대 25분 관찰). 관리형에서 되돌아온 두 번은 모두 컨트롤러가 새로 시작된 직후였습니다(로그 전달 추가로 <code>UPDATING</code>을 거친 86초 뒤, capability 재생성 직후). 주기적인 resync가 아니라 컨트롤러 재시작이 계기로 보이지만, 로그로 확인하지는 않았습니다.<br>
5) OSS에서 같은 IAM 분리를 하려면 feature gate <code>IAMRoleSelector=true</code>와 <code>enableCARM=false</code>를 함께 줘야 합니다. CARM(Cross Account Resource Management)은 IAMRoleSelector 이전의 계정 전환 방식이라 둘을 함께 켤 수 없습니다. feature gate만 켜고 <code>enableCARM</code>을 기본값(true)으로 두면 컨트롤러가 CrashLoopBackOff에 빠집니다.<br>
6) 관리형 컨트롤러 버전은 OSS 최신보다 한 단계 낮았습니다(s3 1.12.2 대 1.13.1). 서비스를 끄는 <code>disabledServices</code>는 AWS CLI 2.38.3에서 동작했지만(약 13초), 이미 설치된 해당 서비스의 CRD는 지워지지 않았습니다. 측정에 처음 쓴 CLI 2.35.11에는 이 설정이 없었습니다.</div>

<nav class="toc" markdown="1">

**목차**

1. [시험 환경과 방법](#env)
2. [관리형 ACK의 구조와 IAM 모델](#arch)
3. [실측 1: 생성과 클러스터에 남는 것](#create)
4. [실측 2: IAMRoleSelector로 팀별 권한 나누기](#iam)
5. [실측 3: 리소스 생성 시간과 태그](#resources)
6. [실측 4: drift 복구, adoption, retain](#lifecycle)
7. [실측 5: Argo CD, kro와 묶기](#together)
8. [데모 영상](#video)
9. [self-managed ACK와 비교](#oss)
10. [얻을 수 있는 것과 비용](#value)
11. [이번 측정의 한계](#limits)
12. [원시 결과, 재현 방법](#more)
13. [시리즈를 마치며](#series)

</nav>

<h2 id="env">1. 시험 환경과 방법</h2>

| 항목 | 값 |
|---|---|
| 클러스터 | Amazon EKS 1.36.4, authenticationMode `API`, 기존 VPC의 private 서브넷, 노드에 public IP 없음 |
| 노드 | m7g.large 2대, 관리형 노드 그룹, `eks-pod-identity-agent` 애드온 |
| 관리형 ACK | `aws eks create-capability --type ACK`, capability role에는 팀 역할 AssumeRole 권한만 |
| self-managed ACK | `oci://public.ecr.aws/aws-controllers-k8s/{s3,sqs,dynamodb}-chart`, 네임스페이스 `ack-system`, Pod Identity로 IAM 역할 1개 연결 |
| 팀 역할 | team-a: `eks-cap-demo-*` 이름의 S3, SQS, DynamoDB 허용. team-b: `eks-cap-demo-*` 이름의 SQS만 허용 |
| 측정 스크립트 | team-a에 Bucket, Queue, Table을 만들고 각각 `ACK.ResourceSynced=True`까지 시간 측정, team-b에 Bucket을 만들어 거부 확인 |

시험 순서는 관리형 ACK 측정, capability 삭제, OSS 컨트롤러 설치와 같은 측정, OSS 제거, 관리형 ACK 재생성과 같은 스크립트 재측정(공정한 비교용), 영상 녹화 순서였습니다.

<h2 id="arch">2. 관리형 ACK의 구조와 IAM 모델</h2>

| 항목 | 관리형 ACK |
|---|---|
| 컨트롤러 위치 | AWS 소유 인프라. 지원하는 모든 서비스 컨트롤러가 한 capability에 들어 있음 |
| 지원 범위 | upstream에서 GA인 컨트롤러만. preview 컨트롤러는 GA가 되면 자동으로 추가됨 |
| 클러스터 접근 | capability role이 액세스 항목(access entry, `AmazonEKSACKPolicy`)으로 API 서버에 접근 |
| AWS 접근 | capability role을 기본으로 쓰고, IAMRoleSelector가 네임스페이스에 맞으면 그 역할을 assume |
| 세션 태그 | 모든 AWS API 호출에 `eks:eks-capability-arn`, `eks:kubernetes-namespace`, `eks:kubernetes-api-group` |
| 삭제 | `RETAIN`만 지원. CRD, CR, AWS 리소스가 모두 남음 |

AWS 문서는 시작할 때 capability role에 서비스 권한을 직접 붙이고, 운영에서는 IAMRoleSelector로 네임스페이스별 역할을 나누라고 권합니다. 이번 시험은 처음부터 운영 방식으로 구성했습니다. capability role에는 아래 권한만 있습니다.

```json
{"Effect": "Allow", "Action": ["sts:AssumeRole", "sts:TagSession"],
 "Resource": ["arn:aws:iam::<ACCOUNT_ID>:role/eks-cap-demo-ack-team-a",
              "arn:aws:iam::<ACCOUNT_ID>:role/eks-cap-demo-ack-team-b"]}
```

<h2 id="create">3. 실측 1: 생성과 클러스터에 남는 것</h2>

| 항목 | 1회차 | 2회차 |
|---|---|---|
| `CREATING`에서 `ACTIVE`까지 | 385.7초 | 77.0초 |
| 보고된 버전 | `46.186.1-eks-1` | `46.186.1-eks-1` |
| 노드에 추가된 Pod | 0 | 0 |
| 설치된 CRD | 267개(새로 설치) | 이미 있음 |
| 추가된 access entry | capability role 1개, `AmazonEKSACKPolicy` | 이전 entry가 남아 있었음 |

1회차와 2회차의 차이는 CRD 설치로 보입니다. 2회차는 capability를 지우고 OSS 컨트롤러를 거친 뒤였는데, RETAIN 정책 때문에 CRD가 그대로 남아 있었습니다.

267개 CRD를 API 그룹으로 묶으면 서비스 API 그룹 66개와 핵심 그룹(`services.k8s.aws`) 하나입니다. CRD가 많은 서비스는 SageMaker(26), EC2(20), Bedrock AgentCore Control(14), API Gateway(11), RDS(10) 순서였습니다. 핵심 그룹에는 `IAMRoleSelector`만 있었고, OSS 차트가 설치하는 `FieldExport` CRD는 없었습니다. ACK 리소스의 값을 ConfigMap이나 Secret으로 내보내는 FieldExport가 필요하다면 관리형에서는 다른 방법(예: 2편의 kro로 status 값을 ConfigMap에 넣기)을 써야 합니다.

capability를 지운 뒤에도(삭제 67.3초) CRD 267개, CR 12개, IAMRoleSelector 2개가 남았고, S3 버킷 같은 AWS 리소스도 그대로였습니다.

<h2 id="iam">4. 실측 2: IAMRoleSelector로 팀별 권한 나누기</h2>

팀 역할의 신뢰 정책은 capability role을 주체로 둡니다. 그리고 클러스터 범위 IAMRoleSelector로 네임스페이스와 역할을 연결합니다.

```yaml
apiVersion: services.k8s.aws/v1alpha1
kind: IAMRoleSelector
metadata: { name: team-b }
spec:
  arn: arn:aws:iam::<ACCOUNT_ID>:role/eks-cap-demo-ack-team-b
  namespaceSelector: { names: [team-b] }
```

team-b에 SQS Queue와 S3 Bucket을 하나씩 만들었습니다. Queue는 8.3초 만에 동기화됐고(capability를 처음 만든 직후라 느린 값입니다), Bucket의 조건은 다음과 같았습니다.

```text
ACK.Recoverable=True   api error AccessDenied: User: arn:aws:sts::<ACCOUNT_ID>:assumed-role/eks-cap-demo-ack-team-b/...
                       is not authorized to perform: s3:ListAllMyBuckets ...
ACK.ResourceSynced=Unknown
Ready=False
ACK.IAMRoleSelected=True  Selected
```

여기서 읽을 수 있는 것은 이렇습니다.

- **거부 메시지에 실제로 쓰인 역할 이름이 나옵니다.** 어느 네임스페이스가 어떤 역할로 호출했는지 CR 조건만 보고 알 수 있어서, 권한 문제를 추적하기 쉽습니다.
- **S3 Bucket 컨트롤러는 먼저 `ListAllMyBuckets`를 부릅니다.** 거부 메시지가 `CreateBucket`이 아니라 `s3:ListAllMyBuckets`였습니다. team-a 역할에는 처음부터 `s3:ListAllMyBuckets`(리소스 `*`)를 넣어 두어서 빼고 시험하지는 않았지만, 이름 패턴으로 버킷 리소스를 좁히더라도 이 목록 조회 권한은 따로 필요해 보입니다.
- **IAMRoleSelector가 없는 네임스페이스는 capability role을 씁니다(문서 기준, 이번에는 시험하지 않음).** 이번처럼 capability role에 서비스 권한을 주지 않으면, 선택자가 없는 네임스페이스에서는 AWS 리소스를 만들 수 없습니다. 기본을 거부로 두는 이 구성이 운영에 안전합니다.

<h2 id="resources">5. 실측 3: 리소스 생성 시간과 태그</h2>

같은 스크립트를 관리형 3회, OSS 2회 돌렸습니다. 관리형은 OSS를 지우고 capability를 다시 만든 뒤에 측정했습니다.

| 리소스 | 관리형 ACK(3회) | self-managed ACK(2회) |
|---|---|---|
| SQS Queue | 1.04~1.06초 | 1.10~1.16초 |
| S3 Bucket | 3.10~3.14초 | 3.20~3.32초 |
| DynamoDB Table | 11.28~11.53초 | 10.15~11.54초 |
| team-b Bucket | `AccessDenied` | `AccessDenied` |

capability를 처음 만든 직후의 첫 측정은 Queue 7.3초, Bucket 10.9초, Table 16.5초로 더 느렸습니다. 반면 다시 만든 capability가 `ACTIVE`가 되고 약 30초 뒤에 잰 관리형 1회차는 Queue 1.06초였으므로, 컨트롤러 기동 직후라서가 아니라 첫 생성 때만 있는 요인(새 역할의 첫 assume 등)으로 보입니다. 비교표에서는 뺐습니다. Table 시간은 대부분 DynamoDB가 테이블을 `ACTIVE`로 만드는 시간입니다.

AWS 리소스에 붙는 기본 태그는 이렇게 달랐습니다.

| 태그 | 관리형 ACK | self-managed ACK |
|---|---|---|
| 컨트롤러 버전 | `eks:controller-version: sqs-v1.7.1` | `services.k8s.aws/controller-version: sqs-1.8.2` |
| 네임스페이스 | `eks:kubernetes-namespace: team-a` | `services.k8s.aws/namespace: team-a` |
| CR 이름 | `eks:kubernetes-resource-name: orders` | 없음 |
| API 그룹 | `eks:kubernetes-api-group: sqs.services.k8s.aws` | 없음 |
| capability ARN | `eks:eks-capability-arn: arn:aws:eks:...:capability/eks-cap-demo/ack/ack/...` | 없음 |

AWS 콘솔에서 어떤 리소스가 어느 클러스터의 어느 CR에서 왔는지 바로 알 수 있다는 점에서는 관리형이 낫습니다. 다만 OSS에서 관리형으로 옮기면 태그 접두사가 `services.k8s.aws/`에서 `eks:`로 바뀝니다. 비용 할당 태그나 태그 기반 IAM 조건이 예전 접두사를 쓰고 있다면 함께 고쳐야 합니다(AWS 문서도 관리형이 리소스를 넘겨받을 때 태그를 바꾸는 API 호출이 한 번 일어난다고 안내합니다).

<h2 id="lifecycle">6. 실측 4: drift 복구, adoption, retain</h2>

ACK가 만든 SQS 큐의 `VisibilityTimeout`을 CLI로 30에서 120으로 바꾸고, 10초마다 다시 30이 되는지 확인했습니다. 최대 25분(1,500초)까지 기다렸습니다.

| 시나리오 | 결과 |
|---|---|
| 관리형 1회차(09:28:38에 변경) | 933초 뒤(09:44:11) 30으로 복구 |
| 관리형 2회차(12:28:07에 변경) | 1,500초 안에 복구 안 됨(120 유지) |
| OSS 1회차(기본값) | 1,500초 안에 복구 안 됨(120 유지) |

처음에는 관리형이 약 15분 주기로 drift를 되돌린다고 읽었습니다. 그런데 2회차는 25분이 지나도 돌아오지 않았습니다. 1회차 시점을 CloudTrail에서 다시 보니, 09:42:45에 ACK 컨트롤러 로그 전달(`PutDeliverySource`)을 추가했고, 이때 capability가 약 7초 동안 `UPDATING` 상태였습니다. 큐가 되돌아온 시각은 그로부터 약 86초 뒤입니다. 같은 시간대에 해당 Queue CR에는 아무 변경도 없었습니다(managedFields 기준). 그래서 1회차 복구는 주기적인 resync가 아니라 컨트롤러 재시작 후의 전체 reconcile 때문으로 보입니다. 같은 일이 한 번 더 있었습니다. OSS 시험에서 120으로 바꿔 둔 큐(OSS는 25분 동안 되돌리지 않음)를, 관리형 capability를 다시 만든 직후인 12:25:27에 ACK가 `SetQueueAttributes`로 30으로 되돌렸습니다. CloudTrail에 남은 ACK의 `SetQueueAttributes` 호출 두 건이 모두 컨트롤러가 새로 시작된 직후였습니다.

여기서 읽을 수 있는 것은 이렇습니다.

- **ACK는 AWS 쪽 변경을 실시간으로 감시하지 않습니다.** CR이 바뀌거나, 컨트롤러가 다시 시작되거나, resync 주기가 돌아와야 AWS의 실제 상태와 비교합니다.
- **OSS의 기본 resync 주기는 10시간입니다.** 차트 기본값이 `reconcile.defaultResyncPeriod: 36000`이고, `reconcile.resourceResyncPeriods`로 리소스별로 줄일 수 있습니다. 대신 AWS API 호출이 늘어납니다.
- **관리형의 resync 주기는 공개되어 있지 않고, 조정할 방법도 없습니다.** 이번 측정으로는 최소 25분보다 길다는 것만 알 수 있습니다.
- 콘솔에서 생긴 drift를 빨리 되돌려야 한다면, ACK에 기대기보다 SCP나 IAM 조건으로 ACK 밖의 변경 자체를 막는 편이 확실합니다. 예를 들어 리소스 태그 `aws:ResourceTag/eks:eks-capability-arn`이 있는 리소스는 세션 태그 `aws:PrincipalTag/eks:eks-capability-arn`이 있는 호출만 바꿀 수 있게 하는 식입니다. 이 정책은 이번에 시험하지 않았습니다.

adoption과 retain도 확인했습니다.

- **adoption:** CLI로 먼저 만든 버킷을 `services.k8s.aws/adoption-policy: adopt-or-create` 주석을 단 Bucket CR로 가져오는 데 4.7초가 걸렸습니다. CR에 `services.k8s.aws/adopted: "true"`가 붙었습니다.
- **retain:** `services.k8s.aws/deletion-policy: retain` 주석을 단 Queue CR을 지운 뒤에도 SQS 큐는 AWS에 남았습니다.
- **capability 삭제 후 이관:** 관리형 capability를 지운 뒤 OSS 컨트롤러를 설치하자, 남아 있던 CR 12개를 그대로 이어받았습니다. 권한 거부로 원래부터 동기화되지 않던 team-b Bucket 2개를 빼고 10개가 `ResourceSynced=True`를 유지했습니다. IAMRoleSelector도 OSS에서 같은 CRD로 동작했습니다.

<h2 id="together">7. 실측 5: Argo CD, kro와 묶기</h2>

세 capability를 한 클러스터에서 함께 쓰면 역할이 자연스럽게 나뉩니다. Argo CD가 Git의 매니페스트를 동기화하고, kro가 여러 리소스를 하나의 API로 묶고, ACK가 그중 AWS 리소스를 만듭니다.

| 조합 | 결과 | 필요했던 추가 설정 |
|---|---|---|
| Argo CD → ACK Queue | Git에 둔 Queue CR이 6.5초 만에 Synced | Argo CD capability role에 `queues.sqs.services.k8s.aws` RBAC |
| kro → ACK Queue, Table + ConfigMap + Deployment | `OrderService` 인스턴스가 13.2~34.3초 만에 ACTIVE, 큐 URL과 테이블 ARN이 ConfigMap에 들어감 | kro capability role에 ACK 리소스 RBAC |

두 경우 모두 함정은 같았습니다. ACK가 설치하는 CRD는 EKS의 기본 access policy(`AmazonEKSEditPolicy` 등)에 포함되지 않으므로, ACK 리소스를 만드는 쪽(Argo CD든 kro든)에 해당 API 그룹 권한을 따로 줘야 합니다. 그리고 2편에서 다룬 이름 충돌 문제는 ACK와 결합할 때 가장 위험합니다. kro가 같은 이름의 기존 Table CR을 가져가 테이블 이름을 바꾸자 ACK가 새 테이블을 만들었고, 원래 테이블은 관리되지 않는 상태로 남았습니다. `deletion-policy: retain`은 CR이 지워질 때 AWS 리소스 삭제를 막아 주지만, 이름이 바뀌어 새 리소스로 옮겨 가는 것은 막지 못합니다. 하위 리소스 이름을 RGD마다 분리하는 것이 근본 대책입니다.

<h2 id="video">8. 데모 영상</h2>

<figure class="video"><video src="https://presentation.atomai.click/eks-capabilities/media/v4-ack.mp4" poster="https://presentation.atomai.click/eks-capabilities/media/v4-ack.jpg" controls preload="metadata" muted playsinline></video><figcaption>관리형 ACK 데모 녹화 영상(31초, 무음, 실제 터미널 녹화, 출력이 없는 대기 구간은 2초로 줄임). capability 상태와 CRD 수, IAMRoleSelector, team-b의 Queue 성공과 Bucket 거부, AWS 리소스에 붙은 태그입니다.</figcaption></figure>

영상은 관리형 ACK를 다시 만든(2회차) 뒤에 녹화했습니다. 화면의 계정 ID는 가렸습니다.

<h2 id="oss">9. self-managed ACK와 비교</h2>

같은 클러스터에서 s3, sqs, dynamodb 컨트롤러를 Helm으로 설치하고 같은 시나리오를 반복했습니다.

| 항목 | 관리형 ACK | self-managed ACK |
|---|---|---|
| 설치 단위 | capability 하나에 서비스 API 그룹 66개 | 서비스마다 Helm release 하나(이번에는 3개) |
| 설치 시간 | 1회차 386초, 2회차 77초 | 3개 합계 46초(설정을 바로잡은 뒤) |
| 노드에서 쓰는 리소스 | 없음 | Pod 3개, 각각 요청 50m CPU, 64Mi(실사용 1m, 15Mi) |
| IAM 연결 | capability role 하나 + IAMRoleSelector | 서비스 계정마다 Pod Identity 연결(이번에는 3개) + IAMRoleSelector |
| 팀별 IAM 분리 | 기본으로 동작 | feature gate `IAMRoleSelector=true`와 `enableCARM=false` 필요 |
| 버전 | AWS가 정함(s3 1.12.2, sqs 1.7.1) | 원하는 버전 선택(s3 1.13.1, sqs 1.8.2) |
| drift 복구 | 주기 비공개, 25분 안에 복구 안 됨(재시작 직후 복구 2회 관찰) | 기본 10시간, 설정으로 조정 가능 |
| AWS 리소스 태그 | `eks:` 접두사 5개 | `services.k8s.aws/` 접두사 2개 |
| 세션 태그 | 자동 설정 | 기본으로 없음 |
| FieldExport | CRD 없음 | 있음 |
| 서비스 끄기 | `disabledServices`(AWS CLI 2.38.3에서 확인, 2.35.11에는 없음). 이미 설치된 CRD는 남음 | 설치하지 않으면 됨 |
| 컨트롤러 로그 | CloudWatch Vended Logs(`EKS_CAPABILITY_ACK_LOGS`) | `kubectl logs`, 직접 수집 |

OSS 설치에서는 한 번 막혔습니다. IAMRoleSelector를 쓰려고 `featureGates.IAMRoleSelector=true`만 주고 `enableCARM`을 기본값으로 두자 컨트롤러가 다음 오류로 CrashLoopBackOff에 빠졌습니다.

```text
Unable to create controller manager ... cannot enable feature gate 'IAMRoleSelector'
when flag 'enable-carm' is set to true
```

차트 기본값이 `enableCARM: true`이기 때문입니다. `--set enableCARM=false`를 함께 주자 3개 컨트롤러가 46초 만에 떴습니다.

관리형의 `disabledServices`도 확인했습니다. AWS 문서는 OSS에서 관리형으로 옮길 때 `--configuration '{"ack": {"disabledServices": [...]}}'`로 이미 직접 운영하는 서비스를 꺼 두라고 권합니다. 처음 측정에 쓴 AWS CLI 2.35.11은 이 설정을 모르는 버전이라 `Unknown parameter in configuration: "ack", must be one of: argoCd`로 요청 자체를 거부했습니다. AWS CLI 2.38.3으로 바꾸자 동작했습니다.

| 시험 | 결과 |
|---|---|
| `disabledServices: ["sagemaker"]`로 변경 | 12.5초 뒤 `ACTIVE`, `describe-capability`에 반영 |
| 변경 후 SageMaker CRD 26개 | 350초가 지나도 그대로 남음 |
| `disabledServices: []`로 되돌림 | 6.8초 뒤 `ACTIVE`, 다른 서비스의 CR은 계속 `ResourceSynced=True` |

문서는 꺼 둔 서비스의 CRD를 설치하지 않는다고 설명하는데, 이미 설치된 CRD를 지우지는 않았습니다. 처음부터 쓰지 않을 서비스를 빼고 싶다면 capability를 만들 때 지정하는 것이 맞아 보입니다(문서 기준, 생성 시 지정은 시험하지 않음). 이 설정을 쓰려면 AWS CLI를 2.37.11 이상으로 올려야 합니다.

정리하면, 관리형은 컨트롤러 수십 개의 설치, 업그레이드, IAM 연결을 한 번에 끝내고, 노드 리소스를 쓰지 않으며, 태그와 세션 태그로 추적이 쉽습니다. 대신 컨트롤러 버전과 resync 주기를 고를 수 없고 upstream보다 늦으며, FieldExport가 없습니다. OSS는 필요한 서비스만 설치하고 버전과 resync 주기를 직접 정할 수 있지만, 서비스가 늘어날 때마다 Helm release, Pod, IAM 연결이 하나씩 늘어나고 각각 업그레이드해야 합니다. 팀별 IAM 분리에는 설정 두 개를 맞춰야 합니다.

<h2 id="value">10. 얻을 수 있는 것과 비용</h2>

관리형 ACK가 잘 맞는 경우는 이렇습니다.

- 여러 AWS 서비스를 Kubernetes에서 다루고 싶은데, 서비스마다 컨트롤러를 운영하기는 부담스러울 때
- 팀이나 네임스페이스마다 IAM 권한을 나눠야 하고, 그 매핑을 CR로 관리하고 싶을 때

반대로 특정 서비스 한두 개만 쓰고, 컨트롤러 버전과 CRD 버전을 직접 관리해야 하거나, FieldExport에 의존하고 있다면 self-managed가 낫습니다.

서울 리전 요금은 capability 시간당 $0.006038, ACK 리소스 하나당 시간당 $0.000061입니다(2026-10-21 AWS Pricing API 조회). 리소스 200개를 한 달(730시간) 유지하면 $4.41 + $8.91로 약 $13.3입니다. self-managed 컨트롤러 3개가 요청한 CPU 150m은 m7g.large(서울 시간당 $0.1003)의 7.5%이므로, 노드 비용으로 나누면 한 달 약 $5.5입니다. 서비스가 10개로 늘면 OSS 쪽은 약 $18이 됩니다. 비용 차이는 작고, 컨트롤러 수에 비례해 늘어나는 운영 부담이 더 큰 차이입니다.

<h2 id="limits">11. 이번 측정의 한계</h2>

- **서비스는 S3, SQS, DynamoDB 세 개만 시험했습니다.** RDS처럼 생성이 오래 걸리거나 의존 관계가 복잡한 리소스는 다루지 않았습니다.
- **drift 복구는 관리형 2회, OSS 1회만 측정했고, 25분까지만 기다렸습니다.** 관리형 복구가 컨트롤러 재시작 때문이라는 해석은 CloudTrail 호출 시각 두 건에 근거한 것이고, 컨트롤러 로그로 확인하지는 않았습니다.
- **교차 계정 리소스 관리는 시험하지 않았습니다.** IAMRoleSelector가 교차 계정에도 쓰인다는 것은 문서 기준입니다.
- **관리형과 OSS를 동시에 띄운 무중단 이전은 시험하지 않았습니다.**
- **리소스 수는 최대 20개 안팎이었습니다.** 수백 개 규모에서의 처리량은 측정하지 않았습니다.

<h2 id="more">12. 원시 결과, 재현 방법</h2>

매니페스트는 [Atom-oh/eks-capabilities-demo](https://github.com/Atom-oh/eks-capabilities-demo)의 `ack/`(네임스페이스, IAMRoleSelector, 팀별 리소스, adoption과 retain 예제)와 `ack-gitops/`(Argo CD가 동기화하는 Queue)에 있습니다. 측정 결과 JSON은 [assets/data/eks-capabilities-2026-10/](/assets/data/eks-capabilities-2026-10/)에 있습니다.

재현 순서는 다음과 같습니다.

1. capability role(서비스 보안 주체 `capabilities.eks.amazonaws.com`)에 팀 역할 AssumeRole 권한만 줍니다.
2. 팀 역할의 신뢰 정책에 capability role을 주체로 넣고, 각 역할에 필요한 서비스 권한을 줍니다(S3는 `s3:ListAllMyBuckets` 포함).
3. `aws eks create-capability --type ACK`로 만들고 `ACTIVE`를 기다립니다.
4. IAMRoleSelector를 적용한 뒤 각 네임스페이스에 리소스를 만듭니다.

시험이 끝난 뒤 ACK 리소스와 retain으로 남긴 AWS 리소스, IAM 역할, 클러스터를 모두 삭제했습니다.

<h2 id="series">13. 시리즈를 마치며</h2>

세 편에서 확인한 공통점은 두 가지입니다. 하나는 컨트롤러를 클러스터 밖으로 빼면 노드 리소스와 업그레이드 부담은 확실히 사라진다는 것입니다. 세 capability 모두 워커 노드에 Pod를 하나도 만들지 않았고, kro와 ACK의 성능은 OSS와 같았습니다. Argo CD는 drift 복구가 같았고, 기본 Git 폴링은 관리형이 더 느렸습니다(1편, self-managed webhook은 측정하지 않음). 다른 하나는 권한이 기본으로 좁다는 것입니다. Argo CD는 배포 대상 권한을, kro는 하위 리소스 권한을, ACK는 서비스별 IAM 권한을 직접 열어야 했습니다. 처음에는 막히지만, 열어 둔 권한이 access entry와 RBAC에 그대로 명시됩니다. OSS는 기본값이 넓어서(kro의 `*/*/*`, Argo CD 컨트롤러의 `*/*/*`), 같은 수준으로 좁히려면(kro의 `rbac.mode: aggregation` 등) 비슷한 작업이 필요합니다.

버전을 고를 수 없다는 점과 일부 기능(Argo CD의 Notifications와 CMP, ACK의 FieldExport)이 빠진다는 점을 받아들일 수 있다면, 플랫폼 컨트롤러를 직접 운영하는 대신 관리형으로 맡기는 쪽이 운영 부담을 줄이는 선택입니다.

<h2 id="refs">참고 자료</h2>

- [Configure ACK permissions](https://docs.aws.amazon.com/eks/latest/userguide/ack-permissions.html)
- [ACK considerations for EKS](https://docs.aws.amazon.com/eks/latest/userguide/ack-considerations.html)
- [Comparing EKS Capability for ACK to self-managed ACK](https://docs.aws.amazon.com/eks/latest/userguide/ack-comparison.html)
- [Access EKS Capabilities controller logs](https://docs.aws.amazon.com/eks/latest/userguide/capabilities-controller-logs.html)
- [Amazon EKS pricing](https://aws.amazon.com/eks/pricing/)
- [ACK documentation](https://aws-controllers-k8s.github.io/community/)
