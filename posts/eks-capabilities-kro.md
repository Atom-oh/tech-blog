---
title: "EKS의 관리형 kro를 직접 써 봤습니다: OSS와 같은 0.9.4, 권한은 직접 열어야 하고 이름 충돌은 조심해야 합니다"
date: 2026-10-21
description: "EKS Capabilities의 kro를 EKS 1.36 클러스터에 만들어 RGD 생성, 인스턴스 수명주기, ACK와의 결합을 측정하고, 같은 시나리오를 self-managed kro 0.9.4로 반복했습니다. 기본 권한으로는 컨트롤러가 시작하지 못하는 문제, 같은 이름의 기존 CR을 덮어써 DynamoDB 테이블을 고아로 만든 사례, OSS 대비 장단점을 정리했습니다."
tags: eks, eks-capabilities, kro, platform-engineering
---

# EKS의 관리형 kro를 직접 써 봤습니다: OSS와 같은 0.9.4, 권한은 직접 열어야 하고 이름 충돌은 조심해야 합니다

<p class="series">EKS Capabilities 직접 써 보기: <a href="/eks-capabilities-argocd/">1편 Argo CD</a> · <strong>2편 kro</strong> · <a href="/eks-capabilities-ack/">3편 ACK</a></p>

<p class="lead">kro(Kube Resource Orchestrator)는 여러 Kubernetes 리소스를 하나의 API로 묶어 주는 도구입니다. EKS Capabilities로 켜면 kro 컨트롤러가 클러스터 밖 AWS 관리 인프라에서 돌고, 클러스터에는 CRD만 남습니다. 이번 편에서는 "컨트롤러를 AWS에 맡기면 무엇이 달라지고, 무엇은 여전히 내가 챙겨야 하나"를 실제 클러스터에서 확인했습니다. 같은 RGD(ResourceGraphDefinition)와 측정 스크립트를 관리형 kro와 self-managed kro 0.9.4에 번갈아 돌렸고, 3편에서 다룰 ACK와 묶은 구성도 함께 시험했습니다. 측정은 2026년 10월 21일에 했고, 원시 결과와 매니페스트는 글 끝에 공개합니다.</p>

<div class="callout"><strong>시험 환경 요약</strong>EKS 1.36.4(ap-northeast-2, 기존 VPC의 private 서브넷), 관리형 노드 그룹 m7g.large 2대, 관리형 kro <code>0.9.4-eks-1</code>, 비교용 self-managed kro 0.9.4(Helm, <code>registry.k8s.io/kro/charts/kro</code>). 워크로드는 nginx Deployment와 Service를 묶은 RGD 하나와, ACK의 SQS Queue와 DynamoDB Table을 함께 묶은 RGD 하나입니다. 시나리오마다 2~5회만 반복했으므로 1초 안팎의 차이는 오차로 봐야 합니다.</div>

<div class="callout warn"><strong>먼저 결론</strong>
1) 관리형 kro는 생성에 70초(2회 모두 70.1~70.4초)가 걸렸고, 버전은 <code>0.9.4-eks-1</code>로 OSS 최신 릴리스와 같았습니다. AWS 문서 예시에 남아 있는 v0.6.3은 오래된 값입니다.<br>
2) 기본으로 붙는 액세스 항목(access entry) 정책 <code>AmazonEKSKROPolicy</code>만으로는, RGD가 만든 API(이번에는 <code>platform.example.com</code> 그룹)를 watch하지 못해 RGD가 <code>ControllerReady=False</code>에서 멈춥니다. 메시지는 "cache sync timeout"이라 권한 문제로 읽히지 않습니다. access entry에 Kubernetes 그룹을 붙이고 RGD별 ClusterRole을 바인딩하자 해결됐습니다. 반대로 OSS Helm 차트는 기본값(<code>rbac.mode: unrestricted</code>)에서 컨트롤러에 <code>*/*/*</code> 권한을 줍니다.<br>
3) 인스턴스 수명주기 성능은 관리형과 OSS가 같았습니다. 생성부터 ACTIVE까지 2.4~3.7초, spec 변경이 Deployment에 반영되기까지 약 0.5초(관리형 1회만 51.6초), 삭제 후 하위 리소스 정리까지 1~4초였습니다.<br>
4) 관리형에서 OSS로, 다시 관리형으로 바꿔도 RGD와 인스턴스가 그대로 이어졌습니다. 단, capability를 지운 동안에는 인스턴스 변경이 반영되지 않습니다.<br>
5) 가장 위험했던 것은 이름 충돌입니다. 같은 이름의 ACK Table CR이 이미 있으면 kro가 그 CR을 가져가 테이블 이름을 덮어썼고, ACK가 새 테이블을 만들면서 원래 테이블은 관리되지 않는 상태로 남았습니다. 인스턴스를 지우자 가져간 CR까지 삭제됐습니다. OSS에서도 똑같이 재현되므로 관리형만의 문제는 아닙니다.</div>

<nav class="toc" markdown="1">

**목차**

1. [시험 환경과 방법](#env)
2. [관리형 kro는 어떻게 동작하나](#arch)
3. [실측 1: 생성과 클러스터에 남는 것](#create)
4. [실측 2: 기본 권한으로는 시작하지 못합니다](#rbac)
5. [실측 3: 인스턴스 수명주기](#lifecycle)
6. [실측 4: ACK와 묶은 OrderService](#ack)
7. [함정: 같은 이름의 기존 CR을 덮어씁니다](#collision)
8. [데모 영상](#video)
9. [self-managed kro와 비교](#oss)
10. [얻을 수 있는 것과 비용](#value)
11. [이번 측정의 한계](#limits)
12. [원시 결과, 재현 방법](#more)

</nav>

<h2 id="env">1. 시험 환경과 방법</h2>

| 항목 | 값 |
|---|---|
| 클러스터 | Amazon EKS 1.36.4, authenticationMode `API`, 기존 VPC의 private 서브넷, 노드에 public IP 없음 |
| 노드 | m7g.large(Graviton, 2 vCPU, 8GiB) 2대, 관리형 노드 그룹 |
| 관리형 kro | `aws eks create-capability --type KRO`, 정책을 붙이지 않은 capability role |
| self-managed kro | Helm `registry.k8s.io/kro/charts/kro` 0.9.4, 네임스페이스 `kro-system`, 기본값 |
| RGD 1 | `NginxApp`: Deployment, Service, 조건부 Ingress(`includeWhen`) |
| RGD 2 | `OrderService`: ACK SQS Queue, ACK DynamoDB Table, 두 리소스의 status 값을 담는 ConfigMap, Deployment |
| 측정 스크립트 | 인스턴스 생성, `status.state=ACTIVE`까지 대기, spec 패치, Deployment 반영 대기, 삭제, 하위 리소스 소멸 대기 |

`NginxApp` RGD는 필자의 학습 문서 [kubernetes-docs](https://github.com/Atom-oh/kubernetes-docs)의 kro 예제를 가져와 한 군데 고쳐서 썼습니다. 원래 예제는 인스턴스가 `ingressClassName`과 호스트 이름을 마음대로 정할 수 있어서, 멀티테넌트 클러스터에서는 다른 팀의 호스트를 가로채는 데 쓰일 수 있습니다. 시험용 RGD에서는 클래스를 고정하고 호스트를 인스턴스 이름과 네임스페이스로 만들도록 바꿨습니다.

시험 순서는 관리형 kro 측정, capability 삭제, OSS kro 설치와 같은 측정, OSS 제거, 관리형 kro 재생성(영상 녹화) 순서였습니다.

<h2 id="arch">2. 관리형 kro는 어떻게 동작하나</h2>

EKS Capabilities는 Argo CD, ACK, kro 세 가지 컨트롤러를 AWS가 운영하는 기능입니다. 공통 구조는 1편에서 자세히 다뤘고, kro에 해당하는 부분만 정리하면 이렇습니다.

| 항목 | 관리형 kro |
|---|---|
| 컨트롤러 위치 | AWS 소유 인프라. 클러스터에는 kro Pod가 없습니다 |
| 클러스터 접근 | capability role(서비스 보안 주체 `capabilities.eks.amazonaws.com`)이 액세스 항목(access entry)으로 API 서버에 접근 |
| IAM 권한 | 필요 없음. kro는 AWS API를 부르지 않습니다 |
| 기본 Kubernetes 권한 | `AmazonEKSKROPolicy`: `kro.run` 그룹과 CRD 관리. RGD 스키마의 group을 따로 정하면(이번 `platform.example.com`) 그 API 권한도 직접 부여해야 함 |
| 버전 | AWS가 정하고 올림. 고정하거나 고를 수 없음 |
| 삭제 | `--delete-propagation-policy RETAIN`만 지원. CRD, RGD, 인스턴스, 하위 리소스가 남음 |

AWS 문서는 kro가 "같은 upstream 컨트롤러를 쓰고 완전히 호환된다"고 설명합니다. 이번 시험에서도 RGD와 CEL 표현식, 측정 결과가 OSS와 같았습니다. 차이는 거의 전부 권한과 운영 쪽에서 나왔습니다.

<h2 id="create">3. 실측 1: 생성과 클러스터에 남는 것</h2>

capability role에는 신뢰 정책만 두고 권한 정책은 붙이지 않았습니다. 명령은 다음과 같습니다.

```bash
aws eks create-capability --cluster-name eks-cap-demo --capability-name kro --type KRO \
  --role-arn arn:aws:iam::<ACCOUNT_ID>:role/eks-cap-demo-kro \
  --delete-propagation-policy RETAIN
```

| 항목 | 1회차 | 2회차 |
|---|---|---|
| `CREATING`에서 `ACTIVE`까지 | 70.1초 | 70.4초 |
| 보고된 버전 | `0.9.4-eks-1` | `0.9.4-eks-1` |
| 노드에 추가된 Pod | 0 | 0 |
| 추가된 CRD | `resourcegraphdefinitions.kro.run`, `graphrevisions.internal.kro.run` | 이미 있음 |
| 추가된 access entry | capability role 1개, `AmazonEKSKROPolicy`(cluster 범위) | 이전 entry가 남아 있었음 |

2회차는 OSS kro를 지운 뒤에 다시 만든 것입니다. 이때 RGD 2개와 인스턴스 4개가 이미 클러스터에 있었는데, 관리형 kro가 별도 조치 없이 모두 이어받아 RGD는 `Active`, 인스턴스는 `ACTIVE`를 유지했습니다.

capability를 지운 뒤에 남는 것도 확인했습니다. 삭제는 46.9초 걸렸고, CRD 4개(kro 2개, RGD가 만든 2개), RGD, 인스턴스, Deployment와 Service가 모두 남았습니다. capability role의 access entry도 남았습니다. 컨트롤러가 없는 동안 인스턴스의 `replicas`를 바꾸면 Deployment는 그대로였고, 이후 OSS kro를 설치하자 밀려 있던 변경이 반영됐습니다.

<h2 id="rbac">4. 실측 2: 기본 권한으로는 시작하지 못합니다</h2>

capability를 만든 직후 `NginxApp` RGD를 적용했습니다. CRD는 1.1초 만에 생겼지만 RGD는 90초가 지나도 `Inactive`였습니다.

```text
GraphAccepted=True Valid
GraphRevisionsResolved=True Resolved
KindReady=True Ready
ControllerReady=False FailedToStart
  add parent handler platform.example.com/v1alpha1, Resource=nginxapps:
  cache sync timeout for platform.example.com/v1alpha1, Resource=nginxapps
```

원인은 권한입니다. `AmazonEKSKROPolicy`는 `kro.run` 그룹과 CRD만 다룹니다. RGD 스키마에서 group을 `platform.example.com`으로 정했기 때문에, 그 API(`nginxapps.platform.example.com`)를 watch할 권한도, 그 아래 Deployment와 Service를 만들 권한도 없습니다. 그런데 메시지에 "forbidden"이 아니라 "cache sync timeout"만 나와서, 처음 보면 컨트롤러 장애로 오해하기 쉽습니다. AWS 문서도 "kro requires additional permissions to create and manage the underlying Kubernetes resources"라고 적고, eksctl 가이드는 시작용으로 `AmazonEKSClusterAdminPolicy`를 붙입니다.

이번에는 cluster-admin 대신 최소 권한으로 풀었습니다. access entry에 Kubernetes 그룹을 붙이고, kubernetes-docs 예제의 RGD별 ClusterRole을 그 그룹에 바인딩했습니다.

```bash
aws eks update-access-entry --cluster-name eks-cap-demo \
  --principal-arn arn:aws:iam::<ACCOUNT_ID>:role/eks-cap-demo-kro \
  --kubernetes-groups kro-workloads
```

```yaml
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: kro-workloads-reviewed-nginxapps
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: kro:controller:reviewed-nginxapps   # nginxapps, deployments, services, ingresses
subjects:
  - kind: Group
    name: kro-workloads
    apiGroup: rbac.authorization.k8s.io
```

바인딩을 적용하고 228초 뒤에 RGD가 `Active`, 인스턴스가 `ACTIVE`가 됐습니다. Deployment와 Service는 그중 마지막 몇 초 사이에 만들어졌으므로, 기다린 시간 대부분은 컨트롤러가 실패한 RGD를 다시 시도하기까지의 간격으로 보입니다. 권한을 미리 준비해 두면 이 대기는 생기지 않습니다. 두 번째 RGD(`OrderService`)는 ClusterRole과 바인딩을 RGD보다 먼저 적용했고, 바로 `Active`가 됐습니다.

여기서 읽을 수 있는 것은 이렇습니다.

- **RGD를 추가할 때마다 kro 쪽 RBAC도 함께 배포해야 합니다.** RGD와 ClusterRole을 같은 Git 경로에 두고 함께 리뷰하는 방식이 자연스럽습니다.
- **같은 일이 Argo CD에도 생깁니다.** Argo CD로 `NginxApp` 인스턴스를 배포하자 `nginxapps.platform.example.com is forbidden`으로 실패했고, Argo CD capability role에도 같은 방식으로 권한을 줘야 했습니다(1편에서 자세히 다룹니다).
- **OSS는 기본값이 반대 방향으로 위험합니다.** OSS 차트의 기본값 `rbac.mode: unrestricted`에서 만들어지는 `kro-cluster-role`은 `apiGroups: ["*"], resources: ["*"], verbs: ["*"]`입니다. RGD를 쓸 수 있는 사람은 사실상 클러스터 전체에 리소스를 만들 수 있습니다. 차트 주석도 `rbac.mode: aggregation`을 권장합니다. 이 모드에서는 `rbac.kro.run/aggregate-to-controller: "true"` 레이블을 단 ClusterRole만 컨트롤러 권한에 합쳐지므로, 관리형과 같은 "RGD마다 권한을 여는" 모델이 됩니다. 하위 호환 때문에 기본값만 `unrestricted`로 남아 있습니다.

<h2 id="lifecycle">5. 실측 3: 인스턴스 수명주기</h2>

같은 스크립트로 `NginxApp` 인스턴스를 만들고(`replicas: 2`), ACTIVE를 기다린 뒤 `replicas: 3`으로 패치하고, 삭제했습니다. 관리형과 OSS를 각각 5회 돌렸습니다.

| 단계 | 관리형 kro(5회) | OSS kro(5회) |
|---|---|---|
| 생성 → `ACTIVE` | 2.49~3.73초 | 2.43~3.44초 |
| spec 변경 → Deployment 반영 | 0.50~0.52초, 1회 51.6초 | 0.49~0.56초 |
| 삭제 → Deployment, Service 소멸 | 1.02~3.02초 | 1.01~4.38초 |
| 삭제 → 인스턴스 소멸 | 2.98~5.56초 | 3.50~5.93초 |

읽을 때 주의할 점이 있습니다.

- **두 쪽의 차이는 오차 범위 안입니다.** 같은 upstream 컨트롤러이므로 예상과 같습니다. 클러스터 밖에서 돈다고 해서 반응이 느려지지는 않았습니다.
- **51.6초 한 번은 원인을 확인하지 못했습니다.** 같은 조건으로 두 번 더 돌렸을 때는 0.5초였습니다. 관리형 컨트롤러의 큐 지연인지 다른 원인인지는 이번 데이터로 구분할 수 없습니다. 컨트롤러 로그(`EKS_CAPABILITY_KRO_LOGS`) 전달은 이 측정 뒤에 설정해서 당시 로그가 없습니다. 운영에서는 처음부터 로그 전달을 켜 두는 것이 좋습니다.
- 생성 시간 2.5초는 대부분 nginx Pod가 Ready가 되는 시간으로 보입니다. 구간별로 나눠 재지는 않았습니다.

<h2 id="ack">6. 실측 4: ACK와 묶은 OrderService</h2>

kro가 특히 쓸모 있는 경우는 AWS 리소스와 Kubernetes 리소스를 한 API로 묶을 때입니다. `OrderService` RGD는 ACK의 SQS Queue와 DynamoDB Table을 만들고, 두 리소스의 status에서 큐 URL과 테이블 ARN을 꺼내 ConfigMap에 넣은 뒤, 그 ConfigMap을 쓰는 Deployment를 띄웁니다.

```yaml
    - id: config
      template:
        apiVersion: v1
        kind: ConfigMap
        metadata:
          name: ${schema.metadata.name + "-aws"}
          namespace: ${schema.metadata.namespace}
        data:
          QUEUE_URL: ${queue.status.queueURL}
          TABLE_ARN: ${table.status.ackResourceMetadata.arn}
```

kro 쪽 ClusterRole에는 `queues.sqs.services.k8s.aws`, `tables.dynamodb.services.k8s.aws`, ConfigMap, Deployment 권한을 넣었습니다. ACK 쪽 IAM 권한은 3편에서 다루는 IAMRoleSelector로 네임스페이스(team-a)에 묶었습니다.

| 인스턴스 | 큐 URL이 status에 나타남 | 테이블 ARN이 status에 나타남 | 인스턴스 `ACTIVE` |
|---|---|---|---|
| checkout | 1.06초 | 22.2초 | 34.3초 |
| checkout-2 | 1.02초 | 1.03초 | 13.2초 |

`ACTIVE`까지 걸린 시간은 대부분 DynamoDB 테이블이 `ACTIVE`가 되는 시간입니다. 테이블에는 `readyWhen: ${table.status.?tableStatus.orValue("") == "ACTIVE"}`를 걸어 두었습니다. 첫 인스턴스(checkout)에서만 테이블 ARN이 22.2초 늦게 나타났고, 두 번째부터는 1초 안이었습니다. 처음 쓰는 리소스 종류의 watch를 준비하는 시간으로 추정하지만 확인하지는 않았습니다.

만들면서 한 번 막힌 곳도 있습니다. Deployment의 `readyWhen`에서 `schema.spec.replicas`를 참조하자 RGD가 `references unknown identifiers: [schema]`로 거부됐습니다. `readyWhen`은 자기 자신만 참조할 수 있으므로 `deployment.spec.replicas`로 바꿔야 합니다.

<h2 id="collision">7. 함정: 같은 이름의 기존 CR을 덮어씁니다</h2>

처음 `OrderService` 인스턴스 이름을 `orders`로 정했는데, team-a 네임스페이스에는 3편 시험에서 kubectl로 만든 ACK Queue `orders`와 Table `orders`가 이미 있었습니다. 이 상태로 인스턴스를 만들자 다음과 같은 일이 생겼습니다.

| 대상 | kro가 한 일 | 결과 |
|---|---|---|
| 기존 Queue CR `orders` | 같은 이름이라 server-side apply로 덮어쓰기 시도 | `spec.queueName: Value is immutable once set`으로 거부, 인스턴스 `ERROR` |
| 기존 Table CR `orders` | 같은 이름이라 덮어씀. `kro.run/owned=true` 레이블이 붙고 `spec.tableName`이 바뀜 | ACK가 새 이름으로 테이블을 새로 만듦. 원래 테이블은 AWS에 남았지만 이를 가리키는 CR이 없음 |
| 인스턴스 삭제 | 자기 소유로 표시한 Table CR 삭제 | 새 테이블은 삭제됨. team-a가 원래 쓰던 Table CR이 사라지고, 원래 테이블은 고아로 남음 |

kro는 대상 이름의 객체가 이미 있어도 거부하지 않았습니다. DynamoDB의 `tableName`이 ACK에서 변경 가능한 필드였기 때문에 피해가 Queue보다 컸습니다. OSS kro 0.9.4로 같은 상황을 다시 만들었을 때도 똑같이 기존 Table CR을 가져가 이름을 바꿨으므로, 관리형 capability가 아니라 upstream 동작입니다.

막는 방법은 이렇습니다.

- **하위 리소스 이름에 RGD 고유 접미사를 붙입니다.** 이번 RGD처럼 `${schema.metadata.name}`만 쓰면 사람이 만든 CR과 겹치기 쉽습니다. 예를 들어 `${schema.metadata.name + "-os"}`처럼 붙입니다. SQS 큐나 DynamoDB 테이블 이름에 Kubernetes 이름에는 쓸 수 없는 `_`를 구분자로 쓰면(S3 버킷 이름은 `_`를 허용하지 않음), 네임스페이스와 이름 조합이 겹치는 것도 막을 수 있습니다. 공개 저장소의 RGD는 이렇게 고쳐 두었습니다.
- **kro가 관리하는 네임스페이스와 사람이 직접 CR을 만드는 네임스페이스를 나눕니다.**
- **데이터가 있는 ACK 리소스에는 `services.k8s.aws/deletion-policy: retain`을 겁니다.** CR이 지워질 때 AWS 리소스 삭제를 막아 줍니다. 다만 이번 사고처럼 이름이 바뀌어 빈 새 테이블로 옮겨 가는 것까지 막지는 못하므로, 근본 대책은 이름 분리입니다(retain과 이 상황의 조합은 시험하지 않았습니다).

시험이 끝난 뒤 고아로 남은 테이블 2개는 직접 지웠습니다.

<h2 id="video">8. 데모 영상</h2>

<figure class="video"><video src="https://presentation.atomai.click/eks-capabilities/media/v3-kro.mp4" poster="https://presentation.atomai.click/eks-capabilities/media/v3-kro.jpg" controls preload="metadata" muted playsinline></video><figcaption>관리형 kro 데모 녹화 영상(46초, 무음, 실제 터미널 녹화, 출력이 없는 대기 구간은 2초로 줄임). capability 상태, access entry 그룹과 바인딩, 인스턴스 생성과 변경, ACK 값이 들어간 ConfigMap, 삭제 순서입니다.</figcaption></figure>

영상은 관리형 kro를 다시 만든(2회차) 직후에 녹화했습니다. 화면의 계정 ID는 가렸습니다.

<h2 id="oss">9. self-managed kro와 비교</h2>

같은 클러스터에 OSS kro 0.9.4를 Helm 기본값으로 설치하고 같은 시나리오를 반복했습니다.

| 항목 | 관리형 kro | self-managed kro |
|---|---|---|
| 설치 | `create-capability`, 70초 | `helm install`, 15.6초 |
| 노드에서 쓰는 리소스 | 없음 | Pod 1개, 요청 256m CPU, 128Mi(실사용 1m, 32Mi) |
| 버전 | `0.9.4-eks-1`, AWS가 올림, 고정 불가 | 원하는 버전으로 고정하고 직접 올림 |
| 컨트롤러 권한 | `kro.run`과 CRD만. RGD의 API와 하위 리소스 권한은 직접 부여 | 차트 기본값 `*/*/*`, `rbac.mode: aggregation`이면 레이블 단 ClusterRole만 |
| 인스턴스 수명주기 성능 | 같음 | 같음 |
| 기존 RGD와 인스턴스 이어받기 | 됨(OSS → 관리형) | 됨(관리형 → OSS) |
| 컨트롤러 로그 | CloudWatch Vended Logs로 CloudWatch Logs, S3, Amazon Data Firehose | `kubectl logs`, 직접 수집 |
| 컨트롤러 설정(플래그, 리소스 한도) | 바꿀 수 없음 | 차트 값으로 조정 |
| 비용 | 서울 리전 capability 시간당 $0.006038 + 인스턴스 시간당 $0.000061 | 노드 리소스(CPU 요청 기준 m7g.large의 약 13%) |

정리하면, 관리형은 컨트롤러 업그레이드와 장애 대응, 노드 리소스를 맡기는 대신 버전을 고를 수 없고, 처음 쓸 때 권한 문제로 한 번은 막힙니다. 권한 모델 자체는 관리형이 더 안전한 쪽에서 시작합니다. OSS는 설치가 빠르고 버전과 설정을 마음대로 정할 수 있지만, 기본값 그대로 쓰면 RGD 작성 권한이 곧 클러스터 관리자 권한이 됩니다. OSS를 `rbac.mode: aggregation`으로 바꾸면 관리형과 비슷한 권한 작업이 필요해지므로, 운영 환경의 권한 작업량은 두 쪽이 크게 다르지 않습니다(aggregation 모드는 차트 설명 기준이며 시험하지 않았습니다).

AWS 문서의 OSS → 관리형 이전 절차는 리더 선출 lease를 `kube-system`으로 옮긴 뒤 capability를 만들라고 안내합니다. 문서 예시는 예전 Helm 경로(`oci://ghcr.io/awslabs/kro/kro-chart`)와 `--set leaderElection.namespace=kube-system`을 쓰는데, 현재 공식 차트는 `oci://registry.k8s.io/kro/charts/kro`이고 0.9.4 차트에서 해당 값의 키는 `config.leaderElectionNamespace`입니다. 이번 시험에서는 두 컨트롤러를 동시에 띄우지 않고 하나를 지운 뒤 다른 하나를 설치했기 때문에 lease 이전은 확인하지 않았습니다.

<h2 id="value">10. 얻을 수 있는 것과 비용</h2>

관리형 kro가 잘 맞는 경우는 이렇습니다.

- 이미 EKS Capabilities의 Argo CD나 ACK를 쓰고 있어서, 플랫폼 컨트롤러를 같은 방식(capability role, access entry, Vended Logs)으로 관리하고 싶을 때
- 플랫폼 팀이 RGD를 만들고 앱 팀은 인스턴스만 쓰는 구조에서, 컨트롤러 운영을 줄이고 싶을 때
- 컨트롤러 권한을 기본으로 좁게 두고 RGD마다 명시적으로 열고 싶을 때

반대로 kro 새 버전의 기능을 바로 써야 하거나, 컨트롤러 플래그와 리소스 한도를 조정해야 한다면 self-managed가 낫습니다.

비용은 작습니다. 서울 리전 기준 capability 요금은 시간당 $0.006038, 인스턴스 요금은 인스턴스 하나당 시간당 $0.000061입니다(2026-10-21 AWS Pricing API 조회). 인스턴스 100개를 한 달(730시간) 유지하면 $4.41 + $4.45로 약 $8.86입니다. self-managed kro가 요청한 256m CPU는 m7g.large(2 vCPU, 서울 시간당 $0.1003)의 12.8%이므로, CPU 요청 기준으로 노드 비용을 나누면 한 달 약 $9.4입니다. 비용만으로는 어느 쪽도 결정적이지 않고, 운영 부담과 권한 모델이 선택 기준이 됩니다.

<h2 id="limits">11. 이번 측정의 한계</h2>

- **시나리오마다 2~5회만 반복했습니다.** 관리형 spec 변경의 51.6초 이상치는 원인을 확인하지 못했습니다.
- **RGD는 2개, 인스턴스는 최대 6개였습니다.** 인스턴스 수백 개에서의 처리량과, AWS 문서([Capability Kubernetes resources](https://docs.aws.amazon.com/eks/latest/userguide/capability-kubernetes-resources.html))가 정한 RGD당 리소스 한도(64개)에 가까운 그래프는 시험하지 않았습니다.
- **두 컨트롤러를 동시에 띄운 무중단 이전은 시험하지 않았습니다.** 한쪽을 지운 뒤 다른 쪽을 설치했습니다.
- **권한 부여 후 228초 대기의 원인은 추정입니다.** 컨트롤러의 재시도 간격으로 보이지만, 컨트롤러 로그로 확인하지는 않았습니다.
- **클러스터는 단일 리전, 단일 계정이었습니다.**

<h2 id="more">12. 원시 결과, 재현 방법</h2>

매니페스트는 [Atom-oh/eks-capabilities-demo](https://github.com/Atom-oh/eks-capabilities-demo)에 있습니다. `kro/`에 RGD, 인스턴스, ClusterRole이 있고, `kro-instances/`는 Argo CD가 동기화하는 인스턴스입니다. 측정 결과 JSON은 [assets/data/eks-capabilities-2026-10/](/assets/data/eks-capabilities-2026-10/)에 있습니다.

재현 순서는 다음과 같습니다.

1. capability role을 만듭니다(서비스 보안 주체 `capabilities.eks.amazonaws.com`, 권한 정책 없음).
2. `aws eks create-capability --type KRO`로 capability를 만들고 `ACTIVE`를 기다립니다.
3. access entry에 Kubernetes 그룹을 붙이고, RGD별 ClusterRole을 그 그룹에 바인딩합니다. RGD보다 먼저 적용합니다.
4. RGD를 적용하고 `kubectl get rgd`에서 `Active`를 확인한 뒤 인스턴스를 만듭니다.

시험 클러스터는 측정이 끝난 뒤 삭제했습니다.

<h2 id="refs">참고 자료</h2>

- [EKS Capabilities](https://docs.aws.amazon.com/eks/latest/userguide/capabilities.html)
- [Configure kro permissions](https://docs.aws.amazon.com/eks/latest/userguide/kro-permissions.html)
- [Comparing EKS Capability for kro to self-managed kro](https://docs.aws.amazon.com/eks/latest/userguide/kro-comparison.html)
- [Access EKS Capabilities controller logs](https://docs.aws.amazon.com/eks/latest/userguide/capabilities-controller-logs.html)
- [Amazon EKS pricing](https://aws.amazon.com/eks/pricing/)
- [kro 0.9.4 release](https://github.com/kubernetes-sigs/kro/releases/tag/v0.9.4)
