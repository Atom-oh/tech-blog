---
title: "EKS의 관리형 Argo CD를 직접 써 봤습니다: 노드에 Pod 0개, webhook 7초 동기화, 배포 권한 함정과 OSS 비교"
date: 2026-10-21
description: "EKS Capabilities의 Argo CD를 EKS 1.36 클러스터에 만들어 생성 시간, 로컬 클러스터 등록과 배포 권한, 폴링과 webhook 동기화, drift 복구, argocd-cm 설정과 Lua health check, CLI 제약, 컨트롤러 로그를 측정했습니다. 같은 클러스터에 self-managed Argo CD 3.4.6을 설치해 같은 시나리오로 비교했습니다."
tags: eks, eks-capabilities, argocd, gitops
---

# EKS의 관리형 Argo CD를 직접 써 봤습니다: 노드에 Pod 0개, webhook 7초 동기화, 배포 권한 함정과 OSS 비교

<p class="series">EKS Capabilities 직접 써 보기: <strong>1편 Argo CD</strong> · <a href="/eks-capabilities-kro/">2편 kro</a> · <a href="/eks-capabilities-ack/">3편 ACK</a></p>

<p class="lead">EKS Capabilities는 Argo CD, kro, ACK를 AWS가 클러스터 밖에서 운영해 주는 기능입니다. 세 가지를 한 편에 다 넣으면 비교가 흐려져서 세 편으로 나눴고, 이번 편은 Argo CD입니다. 궁금했던 것은 "Argo CD를 클러스터 밖으로 빼면 무엇이 편해지고, 무엇이 막히는가"입니다. 관리형 Argo CD를 만들어 GitHub 저장소의 앱을 동기화하고, 같은 클러스터에 self-managed Argo CD 3.4.6(관리형과 같은 버전)을 설치해 같은 시나리오를 반복했습니다. 측정은 2026년 10월 21일에 했고, 원시 결과와 매니페스트는 글 끝에 공개합니다.</p>

<div class="callout"><strong>시험 환경 요약</strong>EKS 1.36.4(ap-northeast-2, 기존 VPC의 private 서브넷), 관리형 노드 그룹 m7g.large 2대, 관리형 Argo CD <code>3.4.6-eks-2</code>, 비교용 self-managed Argo CD 3.4.6(Helm 차트 10.2.2, 기본값). Git 저장소는 GitHub 공개 저장소이고, 앱은 nginx Deployment 하나와 ApplicationSet으로 만든 같은 앱 5개, 2편과 3편에서 쓴 kro, ACK 리소스입니다. 시나리오마다 3회만 반복했으므로 1초 안팎의 차이는 오차로 봐야 합니다.</div>

<div class="callout warn"><strong>먼저 결론</strong>
1) 생성에 349초가 걸렸고, 워커 노드에는 Argo CD Pod가 하나도 생기지 않았습니다. 클러스터에 남은 것은 CRD 3개, <code>argocd</code> 네임스페이스, 액세스 항목(access entry) 하나뿐이었습니다. 같은 기능을 self-managed로 띄우면 Pod 7개, 메모리 339~624Mi를 씁니다.<br>
2) 로컬 클러스터도 EKS ARN으로 직접 등록해야 하고, 자동으로 만들어지는 access entry만으로는 동기화가 아예 되지 않습니다(<code>podtemplates is forbidden</code>). <code>AmazonEKSEditPolicy</code>만 추가해도 실패했고, <code>AmazonEKSAdminViewPolicy</code>를 함께 붙이자 cluster-admin 없이 동작했습니다. kro나 ACK가 만든 새 CRD는 그 위에 RBAC를 따로 줘야 했습니다.<br>
3) Git 폴링은 문서에 6분이라고 되어 있지만, commit부터 Synced까지 342~697초가 걸렸습니다(같은 버전의 self-managed 기본값은 116~171초). GitHub webhook을 연결하자 7.0~7.3초로 줄었습니다. 관리형은 AWS가 호스팅하는 공개 주소가 있으므로, webhook을 받을 엔드포인트를 직접 만들 필요가 없습니다(VPC 엔드포인트로 공개 접근을 막으면 GitHub webhook도 닿지 않을 것으로 보입니다).<br>
4) <code>argocd login</code>은 비밀번호, SSO 모두 실패하고 UI에서 만든 토큰으로만 CLI를 쓸 수 있습니다. <code>argocd-cm</code>의 배너와 Lua health check는 약 12초 만에 반영됐지만, Lua에서 <code>string</code>, <code>os</code> 라이브러리는 쓸 수 없었습니다.<br>
5) 문서에는 sync timeout이 120초로 고정되어 있다고 나오지만, 150초 걸리는 PreSync hook을 넣은 동기화는 161초 만에 정상 완료됐습니다.</div>

<nav class="toc" markdown="1">

**목차**

1. [시험 환경과 방법](#env)
2. [EKS Capabilities는 어떻게 동작하나](#arch)
3. [실측 1: 생성과 클러스터에 남는 것](#create)
4. [실측 2: 로컬 클러스터 등록과 배포 권한 함정](#rbac)
5. [실측 3: 폴링, webhook, drift, ApplicationSet](#sync)
6. [실측 4: 설정, health check, CLI의 범위](#config)
7. [실측 5: 컨트롤러 로그](#logs)
8. [데모 영상](#video)
9. [self-managed Argo CD와 비교](#oss)
10. [얻을 수 있는 것과 비용](#value)
11. [이번 측정의 한계](#limits)
12. [원시 결과, 재현 방법](#more)

</nav>

<h2 id="env">1. 시험 환경과 방법</h2>

| 항목 | 값 |
|---|---|
| 클러스터 | Amazon EKS 1.36.4, authenticationMode `API`, 기존 VPC의 private 서브넷, 노드에 public IP 없음, API 엔드포인트 public 접근은 작업 PC IP로 제한 |
| 노드 | m7g.large(Graviton, 2 vCPU, 8GiB) 2대, 관리형 노드 그룹 |
| 관리형 Argo CD | `aws eks create-capability --type ARGOCD`, IAM Identity Center 조직 인스턴스, Identity Center 사용자 1명을 ADMIN으로 매핑 |
| self-managed Argo CD | Helm `argo/argo-cd` 10.2.2(Argo CD v3.4.6), 기본값, 관리형이 남긴 CRD를 그대로 사용 |
| Git | GitHub 공개 저장소, `main` 브랜치, `apps/demo-web`(ConfigMap, Deployment, Service) |
| 측정 스크립트 | ConfigMap 내용을 바꿔 push한 뒤 Application의 sync revision이 새 commit이 되기까지 시간, Deployment replicas를 손으로 바꾼 뒤 원래대로 돌아오기까지 시간 |

시험 순서는 관리형 Argo CD 측정, kro와 ACK 연동 확인(2편, 3편), UI 녹화, capability 삭제, self-managed 설치와 같은 측정 순서였습니다.

<h2 id="arch">2. EKS Capabilities는 어떻게 동작하나</h2>

EKS Capabilities는 2025년 11월 30일에 정식 출시됐고, 2026년 6월에 컨트롤러 로그 전달, 8월에 Argo CD의 `argocd-cm` 설정 지원이 추가됐습니다. 세 capability의 공통 구조는 이렇습니다.

| 항목 | 내용 |
|---|---|
| 컨트롤러 위치 | AWS 소유 인프라. 워커 노드에 Pod를 만들지 않음 |
| 클러스터 접근 | capability role(서비스 보안 주체 `capabilities.eks.amazonaws.com`)이 EKS 액세스 항목(access entry)으로 API 서버에 접근. 클러스터가 private이어도 동작 |
| 개수 | 클러스터마다 종류별 하나 |
| 버전 | AWS가 정하고 올림. 고정하거나 고를 수 없음 |
| 삭제 | `--delete-propagation-policy RETAIN`만 지원. CRD와 리소스가 남음 |
| 로그 | CloudWatch Vended Logs로 CloudWatch Logs, S3, Amazon Data Firehose에 전달 |
| 요금 | capability 시간당 요금 + 관리하는 리소스 수에 비례한 요금 |

Argo CD에는 몇 가지가 더 붙습니다. 로그인은 IAM Identity Center로만 할 수 있고(로컬 사용자 없음), 권한은 capability 설정의 `rbacRoleMappings`로 Identity Center 사용자나 그룹을 ADMIN, EDITOR, VIEWER에 매핑합니다. UI와 API는 `https://<prefix>.eks-capabilities.<region>.amazonaws.com` 형식의 AWS 호스팅 주소로 열리고, VPC 엔드포인트를 지정하면 공개 접근을 막을 수 있습니다.

<h2 id="create">3. 실측 1: 생성과 클러스터에 남는 것</h2>

```bash
aws eks create-capability --cluster-name eks-cap-demo --capability-name argocd --type ARGOCD \
  --role-arn arn:aws:iam::<ACCOUNT_ID>:role/eks-cap-demo-argocd \
  --delete-propagation-policy RETAIN \
  --configuration '{"argoCd":{"namespace":"argocd",
    "awsIdc":{"idcInstanceArn":"arn:aws:sso:::instance/ssoins-<ID>","idcRegion":"ap-northeast-2"},
    "rbacRoleMappings":[{"role":"ADMIN","identities":[{"id":"<IDC_USER_ID>","type":"SSO_USER"}]}]}}'
```

| 항목 | 결과 |
|---|---|
| `CREATING`에서 `ACTIVE`까지 | 349.0초 |
| 보고된 버전 | `3.4.6-eks-2`(AWS 문서 예시는 `3.1.8-eks-1`) |
| 노드에 추가된 Pod | 0 |
| 추가된 CRD | `applications`, `applicationsets`, `appprojects`(`argoproj.io`) |
| 추가된 네임스페이스 | `argocd`(안에는 빈 `argocd-webhook-creds-secret` 하나) |
| 추가된 access entry | capability role, `AmazonEKSArgoCDClusterPolicy`(cluster 범위), `AmazonEKSArgoCDPolicy`(`argocd` 네임스페이스) |
| 서버 URL | `https://argocd-<id>-<ACCOUNT_ID>.eks-capabilities.ap-northeast-2.amazonaws.com` |

측정에 처음 쓴 AWS CLI 2.35.11은 `--configuration`의 `argoCd` 아래 `namespace`, `awsIdc`, `rbacRoleMappings`, `networkAccess` 네 필드만 알았습니다. 측정일 기준 최신 CLI(2.38.3)에는 UI 주소 접두사를 정하는 `endpointPrefix`와 ACK용 설정이 추가되어 있습니다. 문서의 예시를 그대로 쓰려면 CLI부터 최신으로 올리는 것이 좋습니다.

capability를 지우는 데는 100.5초가 걸렸습니다. 지운 뒤에도 CRD 3개, Application 9개, ApplicationSet, 클러스터 Secret, 배포된 워크로드가 모두 남았고, capability role의 access entry도 남았습니다. RETAIN 정책 그대로입니다.

<h2 id="rbac">4. 실측 2: 로컬 클러스터 등록과 배포 권한 함정</h2>

관리형 Argo CD는 자기가 붙은 클러스터도 자동으로 배포 대상에 넣지 않습니다. Kubernetes API 주소가 아니라 EKS ARN으로 클러스터 Secret을 만들어야 합니다.

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: eks-cap-demo
  namespace: argocd
  labels:
    argocd.argoproj.io/secret-type: cluster
stringData:
  name: eks-cap-demo
  server: arn:aws:eks:ap-northeast-2:<ACCOUNT_ID>:cluster/eks-cap-demo
  project: default
```

이 Secret과 Application을 만들었지만 동기화가 시작되지 않았습니다. 권한을 하나씩 늘려 가며 확인했습니다.

| capability role의 클러스터 권한 | 결과 |
|---|---|
| 자동 access entry만(`AmazonEKSArgoCDClusterPolicy`, `AmazonEKSArgoCDPolicy`) | `ComparisonError: ... podtemplates is forbidden ... cannot list resource "podtemplates" ... at the cluster scope` |
| + `AmazonEKSEditPolicy`(cluster 범위) | 141초 기다려도 실패. `securitygrouppolicies.vpcresources.k8s.aws` 목록 조회 거부 |
| + `AmazonEKSAdminViewPolicy`(cluster 범위) | refresh 후 11.6초 만에 Synced, Healthy |

AWS 문서도 "자동으로 만들어지는 access entry는 애플리케이션을 배포할 권한을 주지 않는다"고 적고 있고, 예시에서는 `system:masters`나 cluster-admin을 씁니다. 실패 원인은 Argo CD의 동작 방식에 있습니다. Argo CD는 배포할 리소스만 보는 것이 아니라, 클러스터의 모든 리소스 타입을 캐시하려고 목록 조회를 합니다. Edit 정책에는 CRD 같은 리소스의 목록 조회 권한이 없어서 캐시 동기화 단계에서 막혔고, 읽기 전용인 AdminView를 함께 주자 해결됐습니다.

권한 문제는 여기서 끝나지 않았습니다. 2편의 kro 인스턴스(`NginxApp`)를 Argo CD로 배포하자 다시 실패했습니다.

```text
nginxapps.platform.example.com is forbidden: User "arn:aws:sts::<ACCOUNT_ID>:assumed-role/eks-cap-demo-argocd/..."
cannot create resource "nginxapps" in API group "platform.example.com" in the namespace "example"
```

EKS의 기본 access policy는 kro가 만든 API나 ACK CRD를 포함하지 않습니다. access entry에 Kubernetes 그룹(`argocd-platform`)을 붙이고, 해당 API 그룹 권한만 담은 ClusterRole을 바인딩하자 16초 만에 Synced가 됐습니다. 3편의 ACK Queue도 같은 방식으로 권한을 열었습니다.

여기서 읽을 수 있는 것은 이렇습니다.

- **관리형은 기본으로 아무것도 배포할 수 없습니다.** 처음에 권한을 설계해야 하지만, 그만큼 Argo CD가 무엇을 할 수 있는지가 access entry와 RBAC에 명시적으로 남습니다.
- **cluster-admin이 꼭 필요하지는 않았습니다.** Edit과 AdminView 조합에, 플랫폼 CRD별 ClusterRole을 더하는 방식으로 충분했습니다. 더 좁히려면 `argocd-cm`의 `resource.respectRBAC`(관리형에서 지원하는 키)로 권한이 있는 리소스만 감시하게 하는 방법도 있습니다. 이번에는 시험하지 않았습니다.
- **self-managed Helm 차트는 반대입니다.** application-controller의 ClusterRole이 `apiGroups: ["*"], resources: ["*"], verbs: ["*"]`여서 이런 문제가 생기지 않지만, Argo CD가 곧 클러스터 관리자입니다.

<h2 id="sync">5. 실측 3: 폴링, webhook, drift, ApplicationSet</h2>

`apps/demo-web/configmap.yaml`의 버전 문자열을 바꿔 push하고, Application의 sync revision이 그 commit이 되기까지 시간을 쟀습니다. 관리형은 기본 폴링으로 3회, GitHub webhook을 연결한 뒤 3회 측정했습니다.

| 방식 | 1회 | 2회 | 3회 |
|---|---|---|---|
| 관리형, 기본 폴링 | 619.3초 | 341.8초 | 697.1초 |
| 관리형, GitHub webhook | 7.1초 | 7.0초 | 7.3초 |
| self-managed, 기본 폴링 | 143.0초 | 171.4초 | 115.7초 |

AWS 문서는 "기본으로 6분마다 Git 저장소를 폴링한다"고 설명하지만, 관리형에서 측정한 값은 342초에서 697초까지 퍼져 있었습니다. upstream Argo CD는 정해진 주기(`timeout.reconciliation`)에 무작위 jitter를 더해 refresh합니다. self-managed 차트 기본값은 120초와 60초였고, 실측도 116~171초로 그 범위 안이었습니다. 관리형의 jitter 값은 공개되어 있지 않고, 측정값의 최댓값(697초)으로 보면 6분 주기에 jitter가 수 분 더해지는 것으로 보입니다. 이 키는 관리형에서 바꿀 수 있는 `argocd-cm` 목록에 없습니다. 배포 지연이 수 분 단위로 흔들려도 괜찮지 않다면 webhook은 사실상 필수입니다.

webhook 설정은 간단했습니다. capability가 만든 `argocd-webhook-creds-secret`의 `webhook.github.secret`에 무작위 문자열을 넣고, GitHub 저장소에 `<서버 URL>/api/webhook`을 push 이벤트로 등록하면서 Secret 칸에도 같은 값을 넣었습니다. GitHub의 delivery는 모두 200으로 응답했습니다. self-managed였다면 GitHub가 접근할 수 있는 공개 HTTPS 엔드포인트를 따로 만들어야 합니다(이번 환경은 인터넷 대상 로드밸런서를 만들지 않는 정책이라 self-managed webhook은 측정하지 않았습니다).

drift와 ApplicationSet 결과는 다음과 같습니다.

| 시나리오 | 관리형 | self-managed |
|---|---|---|
| Deployment를 5개로 늘린 뒤 2개로 돌아오기까지(selfHeal) | 2.08, 2.08, 2.32초 | 3.06, 1.99, 1.05초 |
| ApplicationSet(list generator)으로 앱 5개 생성 → 모두 Synced, Healthy | 6.1초 | 측정 안 함 |
| 기존 Application 9개를 이어받아 모두 Synced, Healthy | 해당 없음 | 설치 시작부터 43.9초 |

drift 복구는 둘 다 수 초 안이었습니다. self-managed는 관리형이 남긴 Application 9개를 설치 직후 그대로 이어받았습니다. 바꾼 것은 클러스터 Secret의 `server`를 EKS ARN에서 `https://kubernetes.default.svc`로 고친 것 하나입니다.

<h2 id="config">6. 실측 4: 설정, health check, CLI의 범위</h2>

관리형 Argo CD는 2026년 8월부터 `argocd-cm`의 일부 키를 읽습니다. ConfigMap 이름은 `argocd-cm`, 위치는 capability 네임스페이스, 레이블 `app.kubernetes.io/part-of: argocd`가 필요합니다. 지원하지 않는 키는 오류 없이 무시됩니다.

| 시험 | 결과 |
|---|---|
| `ui.bannercontent` | UI 상단에 배너 표시(영상에서 확인) |
| `resource.customizations.health.platform.example.com_NginxApp`(kro 인스턴스용 Lua) | 적용됨. 스크립트 메시지를 바꾸면 약 12초 뒤 반영 |
| Lua에서 `string.upper("ok")` | `attempt to index a non-table object(nil) with key 'upper'`, 리소스 health `Unknown` |
| Lua에서 `os.time()` | 같은 오류, health `Unknown` |
| Lua에서 `tostring(42)` | 동작 |
| ACK Queue(health check 정의 없음) | 내장 health check로 `Healthy` |
| `resource.customizations.health._ConfigMap`(코어 리소스) | 효과 확인 못 함 |

`useOpenLibs`는 upstream에서도 기본으로 꺼져 있습니다. 같은 스크립트를 self-managed 3.4.6(기본값)에 넣어 보니 `os.time()`과 `string.upper()` 모두 같은 오류로 `Unknown`이 됐습니다. 기본 상태의 동작은 같고, 차이는 self-managed에서는 리소스 종류별로 `useOpenLibs`를 켤 수 있고 관리형에서는 켤 수 없다는 점입니다. 기존 Lua 스크립트가 `string.find` 같은 함수를 쓴다면 관리형으로 옮기기 전에 고쳐야 합니다. 스크립트 오류는 Argo CD 전체를 멈추지 않고, 해당 리소스의 health가 `Unknown`이 되면서 Application에 오류 조건이 붙었습니다.

한 가지 측정 실수도 적어 둡니다. 처음에는 `kubectl get application -o json`의 `.status.resources[].health`가 비어 있어서 "health check가 적용되지 않는다"고 판단했습니다. Argo CD 3.x는 리소스 health를 기본으로 Application status에 저장하지 않으므로, Deployment조차 비어 있었습니다. `argocd app get`이나 UI로 다시 확인하자 모두 정상이었습니다.

CLI도 확인했습니다.

| 명령 | 결과 |
|---|---|
| `argocd login <server> --username admin` | `Invalid username or password` |
| `argocd login <server> --sso` | Identity Center OIDC discovery 404로 실패 |
| UI에서 만든 토큰 + `ARGOCD_SERVER`, `ARGOCD_AUTH_TOKEN`, `ARGOCD_OPTS=--grpc-web` | `app list`, `app get`, `app sync`(8.6초), `cluster list`, `proj list`, `account can-i` 모두 동작 |
| `argocd account list` | `admin`이 아니라 Identity Center 사용자 ID가 계정으로 보임(`apiKey` 권한) |
| `argocd version` | 서버 `v3.4.6+eks-2` |

AWS 문서는 sync timeout이 120초로 고정되어 바꿀 수 없다고 설명합니다. 그래서 150초 동안 sleep하는 PreSync hook Job을 넣은 앱을 동기화해 봤는데, hook이 끝난 뒤 나머지 리소스까지 적용되어 161초 만에 `Succeeded`가 됐습니다. 이번 시험에서는 120초 제한이 hook을 포함한 전체 동기화에 걸리지 않았습니다. 어떤 단계에 적용되는 제한인지는 문서만으로는 알 수 없어서, 긴 hook에 의존한다면 직접 확인해 보는 것이 좋습니다.

문서상 지원하지 않는 기능은 Config Management Plugin(CMP, Helm이나 Kustomize 외의 매니페스트 생성 도구를 붙이는 플러그인), Notifications 컨트롤러, 사용자 정의 SSO, UI 확장, `argocd admin` 명령 등입니다. 이번에는 Notifications와 CMP를 따로 시험하지 않았고, 관리형 로그 유형에도 Notifications가 없다는 것만 확인했습니다. self-managed에는 Notifications 컨트롤러 Pod가 기본으로 떠 있었습니다.

<h2 id="logs">7. 실측 5: 컨트롤러 로그</h2>

컨트롤러가 클러스터 밖에 있어서 `kubectl logs`로는 볼 수 없습니다. 대신 2026년 6월에 추가된 Vended Logs로 CloudWatch Logs에 보낼 수 있습니다. Argo CD는 `EKS_CAPABILITY_ARGOCD_APPLICATION_LOGS`, `..._APPLICATIONSET_LOGS`, `..._COMMITSERVER_LOGS`, `..._REPOSERVER_LOGS`, `..._SERVER_LOGS` 다섯 유형이 있고, 이번에는 application, server, repo server 세 가지를 연결했습니다.

```bash
aws logs put-delivery-source --name eks-cap-demo-argocd-application \
  --resource-arn <CAPABILITY_ARN> --log-type EKS_CAPABILITY_ARGOCD_APPLICATION_LOGS
aws logs create-delivery --delivery-source-name eks-cap-demo-argocd-application \
  --delivery-destination-arn <DELIVERY_DESTINATION_ARN>
```

로그 소스를 연달아 추가하자 두 번째부터 `ConflictException: Capability argocd cannot be updated as it is currently in UPDATING state`가 났습니다. 로그 소스를 하나 추가할 때마다 capability가 약 6.6초 동안 `UPDATING`이 되므로, 하나씩 추가하고 `ACTIVE`를 기다려야 합니다. 3편의 ACK에서는 이 업데이트 직후 컨트롤러가 다시 시작된 것으로 보이는 정황이 있었습니다. Argo CD에서는 확인하지 않았습니다.

로그는 구조화된 JSON으로 들어왔습니다. 동기화 한 번의 단계별 소요 시간까지 남습니다.

```json
{"resource_arn":"arn:aws:eks:ap-northeast-2:<ACCOUNT_ID>:capability/eks-cap-demo/argocd/argocd/...",
 "level":"info","message":"Reconciliation completed","application":"kro-instances",
 "dest-name":"eks-cap-demo","dest-namespace":"example","comparison-level":3,
 "time_ms":476,"compare_app_state_ms":446,"patch_ms":24}
```

<h2 id="video">8. 데모 영상</h2>

<figure class="video"><video src="https://presentation.atomai.click/eks-capabilities/media/v1-argocd.mp4" poster="https://presentation.atomai.click/eks-capabilities/media/v1-argocd.jpg" controls preload="metadata" muted playsinline></video><figcaption>관리형 Argo CD 터미널 데모(37초, 무음, 실제 터미널 녹화, 출력이 없는 대기 구간은 2초로 줄임). capability 목록, 노드의 Pod, ARN으로 등록한 클러스터, git push부터 webhook 동기화까지 7.0초, drift 복구입니다. 화면의 <code>kro-system</code> Pod는 2편의 self-managed kro 비교용입니다.</figcaption></figure>

<figure class="video"><video src="https://presentation.atomai.click/eks-capabilities/media/v2-argocd-ui.mp4" poster="https://presentation.atomai.click/eks-capabilities/media/v2-argocd-ui.jpg" controls preload="metadata" muted playsinline></video><figcaption>관리형 Argo CD UI(34초, 무음). <code>argocd-cm</code>으로 넣은 배너, 앱 목록, demo-web 리소스 트리와 수동 Sync, Lua health check가 적용된 kro 인스턴스, 내장 health check가 적용된 ACK Queue입니다. 커밋 작성자 이메일은 가렸습니다.</figcaption></figure>

<h2 id="oss">9. self-managed Argo CD와 비교</h2>

관리형을 지운 뒤 같은 클러스터에 Helm 차트 10.2.2(Argo CD v3.4.6)를 기본값으로 설치했습니다.

| 항목 | 관리형 Argo CD | self-managed Argo CD |
|---|---|---|
| 설치 | `create-capability`, 349초 | `helm install`, 43.4초 |
| 노드에서 쓰는 리소스 | 없음 | Pod 7개(application-controller, applicationset, repo-server, server, redis, dex, notifications), 유휴 시 CPU 7~16m, 메모리 339~435Mi, 동기화 중 81m, 624Mi. 차트 기본값에는 resource request가 없음 |
| 버전 | `3.4.6-eks-2`, AWS가 올림 | 원하는 버전. 측정일 기준 최신은 v3.5.5 |
| 로그인 | IAM Identity Center만. capability 설정으로 그룹 매핑 | 로컬 admin, Dex, OIDC 등. Identity Center는 SAML 앱과 Dex 설정을 직접 구성 |
| UI와 API 주소 | AWS가 호스팅하는 HTTPS 주소. VPC 엔드포인트로 비공개 가능 | 직접 노출(Ingress, 로드밸런서, 인증서, DNS) |
| Git webhook | 바로 사용(7초) | GitHub가 접근할 공개 엔드포인트가 필요 |
| 폴링 | 문서상 6분, 실측 342~697초 | 기본 120초 + jitter 60초, 실측 116~171초 |
| 배포 권한 | 직접 부여(Edit + AdminView + CRD별 RBAC) | 차트 기본값이 `*/*/*` |
| 원격 클러스터 | EKS ARN과 access entry만으로 등록, private 클러스터도 네트워크 설정 없이 가능 | 클러스터마다 API 주소, 인증 설정, 네트워크 경로 |
| 설정 범위 | 지원하는 `argocd-cm` 키만, Lua 표준 라이브러리 없음 | 모든 설정 |
| 확장 | CMP, Notifications, UI 확장 없음 | 모두 가능 |
| CLI | 토큰만, `argocd admin` 없음 | 전체 |
| 컨트롤러 로그 | Vended Logs | `kubectl logs`, 직접 수집 |

Identity Center 연동은 Argo CD의 Identity Center 연동 문서와 이를 정리한 필자의 학습 문서([kubernetes-docs](https://github.com/Atom-oh/kubernetes-docs)의 GitOps 멀티 클러스터 장)를 기준으로 비교했고, 직접 구성하지는 않았습니다. self-managed는 Identity Center에 고객 관리형 SAML 2.0 애플리케이션을 만들고, ACS URL과 audience를 Argo CD 도메인에 맞추고, 서명 인증서를 받아 Dex 커넥터 설정에 넣고, `argocd-rbac-cm`에 이메일 기준 매핑을 써야 합니다. 그룹 속성 매핑은 AWS가 공식 지원하지 않는 우회 방법입니다. 관리형은 `awsIdc`와 `rbacRoleMappings` 두 필드로 끝났습니다. 이번에는 사용자 한 명만 매핑했지만, 토큰의 사용자 정보에 Identity Center 그룹 ID가 함께 들어와 있었고, 문서상 그룹(`SSO_GROUP`)도 매핑할 수 있습니다.

정리하면, 관리형은 설치, 업그레이드, HA, 인증서와 공개 주소, SSO 구성을 모두 없애 주고, webhook을 바로 받을 수 있으며, 원격 클러스터와 private 클러스터 연결을 네트워크 설정 없이 access entry와 그 권한으로 처리합니다(원격 등록은 문서 기준). 대신 기본 권한이 없어서 처음에 반드시 막히고, 플랫폼 CRD가 늘 때마다 RBAC를 더해야 합니다. 버전을 고를 수 없어 upstream보다 한 단계 늦고, Notifications, CMP, `argocd admin`, Lua 표준 라이브러리를 쓸 수 없으며, 기본 폴링 간격이 길고 흔들립니다. self-managed는 같은 버전이면 동작이 같았고(기본 폴링 간격만 더 짧음) 모든 기능과 설정을 쓸 수 있지만, Pod 7개를 직접 운영하고, UI와 webhook을 위한 노출, SSO, 원격 클러스터 인증을 직접 만들어야 하며, 기본 권한이 클러스터 관리자 수준입니다.

<h2 id="value">10. 얻을 수 있는 것과 비용</h2>

관리형 Argo CD가 잘 맞는 경우는 이렇습니다.

- 여러 EKS 클러스터, 특히 private 클러스터나 다른 계정의 클러스터에 배포하는 허브를 만들 때
- Argo CD UI를 사내 Identity Center 계정으로만 열고 싶은데, SAML과 Dex를 직접 운영하고 싶지 않을 때
- Argo CD 업그레이드와 HA 구성을 맡길 곳이 필요할 때

반대로 Notifications로 배포 알림을 보내고 있거나, CMP로 사내 템플릿 도구를 붙였거나, `string` 같은 Lua 라이브러리를 쓰는 health check가 많다면 self-managed를 유지하는 편이 낫습니다.

서울 리전 요금은 capability 시간당 $0.034799, Application 하나당 시간당 $0.001726입니다(2026-10-21 AWS Pricing API 조회). Application은 대상 클러스터별로 셉니다. 앱 9개를 한 달(730시간) 유지하면 $25.40 + $11.34로 약 $36.7, 앱 100개면 $25.40 + $126.00으로 약 $151입니다. self-managed는 이번 기본 설치에서 메모리를 최대 624Mi 썼습니다. m7g.large(8GiB, 서울 시간당 $0.1003)의 7.6%이므로 메모리 기준으로 노드 요금을 나누면 한 달 약 $5.6입니다. HA 구성이나 앱 수 증가에 따라 이 값은 커집니다. 비용만 보면 앱 9개에서도 self-managed가 약 7분의 1이고, 앱 수가 늘수록 차이가 커집니다. 관리형을 고르는 이유는 비용이 아니라 노출, SSO, 업그레이드, 멀티 클러스터 연결을 운영하지 않아도 된다는 점입니다.

<h2 id="limits">11. 이번 측정의 한계</h2>

- **시나리오마다 3회만 반복했습니다.** 폴링 간격처럼 분포가 넓은 값은 3회로는 범위만 보여 줄 수 있습니다.
- **앱은 최대 9개였습니다.** 앱 수백 개나 원격 클러스터 여러 개에서의 처리량은 측정하지 않았습니다(관리형 한도는 Application 3,000개, 원격 클러스터 100개).
- **self-managed는 기본값 단일 인스턴스였습니다.** HA 구성의 리소스 사용량은 이보다 큽니다. self-managed webhook은 공개 엔드포인트를 만들지 않아 측정하지 않았습니다.
- **Notifications, CMP, VPC 엔드포인트 비공개 접근, 원격 클러스터 등록은 시험하지 않았습니다.** 해당 내용은 문서 기준입니다.
- **sync timeout 120초가 어디에 적용되는지는 확인하지 못했습니다.** hook 하나가 150초 걸리는 경우만 시험했습니다.

<h2 id="more">12. 원시 결과, 재현 방법</h2>

매니페스트는 [Atom-oh/eks-capabilities-demo](https://github.com/Atom-oh/eks-capabilities-demo)에 있습니다. `apps/`에 앱, `argocd/`에 Application과 ApplicationSet이 있고, destination 클러스터 이름은 `eks-cap-demo`입니다. 측정 결과 JSON은 [assets/data/eks-capabilities-2026-10/](/assets/data/eks-capabilities-2026-10/)에 있습니다.

재현 순서는 다음과 같습니다.

1. capability role을 만듭니다(서비스 보안 주체 `capabilities.eks.amazonaws.com`, 이번 시험처럼 공개 GitHub만 쓰면 권한 정책 없음).
2. Identity Center 인스턴스 ARN과 사용자나 그룹 ID로 `aws eks create-capability --type ARGOCD`를 실행하고 `ACTIVE`를 기다립니다.
3. capability role의 access entry에 배포 권한을 붙입니다(이번에는 `AmazonEKSEditPolicy`와 `AmazonEKSAdminViewPolicy`, 플랫폼 CRD용 ClusterRole).
4. EKS ARN으로 클러스터 Secret을 만들고 Application을 적용합니다.
5. 빠른 동기화가 필요하면 `argocd-webhook-creds-secret`의 `webhook.github.secret`에 무작위 문자열을 넣고, Git 저장소에 `<서버 URL>/api/webhook`을 같은 secret으로 등록합니다.

시험 클러스터와 GitHub webhook은 측정이 끝난 뒤 삭제했습니다.

<h2 id="refs">참고 자료</h2>

- [EKS Capabilities](https://docs.aws.amazon.com/eks/latest/userguide/capabilities.html)
- [Argo CD considerations](https://docs.aws.amazon.com/eks/latest/userguide/argocd-considerations.html)
- [Comparing EKS Capability for Argo CD to self-managed Argo CD](https://docs.aws.amazon.com/eks/latest/userguide/argocd-comparison.html)
- [Configure Argo CD settings](https://docs.aws.amazon.com/eks/latest/userguide/argocd-configure-settings.html)
- [Capability Kubernetes resources](https://docs.aws.amazon.com/eks/latest/userguide/capability-kubernetes-resources.html)
- [Access EKS Capabilities controller logs](https://docs.aws.amazon.com/eks/latest/userguide/capabilities-controller-logs.html)
- [Amazon EKS Capabilities 출시 발표(2025-11-30)](https://aws.amazon.com/about-aws/whats-new/2025/11/amazon-eks-capabilities/)
- [Amazon EKS pricing](https://aws.amazon.com/eks/pricing/)
