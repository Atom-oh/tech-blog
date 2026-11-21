---
title: "EKS 노드 역할에 붙인 앱 권한을 IRSA와 Pod Identity로 옮겨 봤습니다: 같은 앱 세 벌로 비교한 동작 원리와 함정"
date: 2026-11-21
description: "노드 인스턴스 프로파일에 앱 권한을 붙여 운영하던 EKS 클러스터에서 같은 앱 세 벌을 IRSA와 EKS Pod Identity로 각각 옮기고, 노드 권한을 회수해 결과를 비교했습니다. 두 방식이 자격 증명을 받는 경로, 주입되는 변수와 토큰의 차이, association과 IAM의 eventual consistency 함정을 실측으로 정리했습니다."
tags: eks, iam, irsa, pod-identity, security
---

# EKS 노드 역할에 붙인 앱 권한을 IRSA와 Pod Identity로 옮겨 봤습니다: 같은 앱 세 벌로 비교한 동작 원리와 함정

<p class="lead">EKS를 처음 운영할 때 가장 쉬운 길은 앱에 필요한 S3·DynamoDB 권한을 노드 IAM 역할에 붙이는 것입니다. 잘 동작하지만, 그 노드의 모든 Pod가 같은 권한을 갖게 됩니다. 이번에는 이 상태에서 출발해 같은 앱 세 벌 중 하나는 그대로 두고, 하나는 IRSA로, 하나는 EKS Pod Identity로 옮긴 뒤 노드 역할에서 권한을 지웠습니다. 화면에서 무엇이 바뀌는지, 두 방식이 안쪽에서 어떻게 다르게 움직이는지, 리허설에서 걸린 함정은 무엇인지 정리합니다. 측정은 2026년 11월 21일 서울 리전의 EKS 1.36 클러스터에서 했습니다.</p>

<div class="callout"><strong>시험 환경 요약</strong>기존 EKS 1.36 클러스터(ap-northeast-2, 기존 VPC의 private 서브넷)에 데모 전용 관리형 노드 그룹 1대(t3.medium, Amazon Linux 2023, IMDSv2 필수·hop limit 2, taint 적용)를 추가했습니다. orders 앱 세 벌은 <code>public.ecr.aws/aws-cli/aws-cli:2.35.11</code> 이미지로 3초마다 STS <code>GetCallerIdentity</code>와 S3 <code>GetObject</code>를 부르고 결과를 JSON 한 줄로 남깁니다. 노드 역할, IRSA 역할, Pod Identity 역할 모두 같은 객체 하나의 <code>s3:GetObject</code>만 허용합니다.</div>

<div class="callout warn"><strong>먼저 결론</strong>
1) 두 방식 모두 <strong>코드 변경 없이</strong> 전환됐습니다. AWS SDK의 기본 자격 증명 체인에서 IRSA(웹 ID)와 Pod Identity(컨테이너 공급자)가 IMDS보다 앞에 있기 때문입니다. 필요한 것은 설정과 Pod 재생성뿐이었습니다. 고친 스크립트로 처음부터 끝까지 돌린 두 번째 리허설에서 IRSA는 6초, Pod Identity는 11초가 걸렸습니다.<br>
2) 노드 역할에서 S3 정책을 지우자 <strong>13초 뒤</strong> 레거시 복사본만 거부됐고, 나머지 둘은 계속 읽었습니다. 다만 약 4분 30초 뒤에 허용 응답이 두 번 더 나왔습니다. IAM 회수는 수 분 동안 지켜보고 판단해야 합니다.<br>
3) 첫 리허설에서 Pod Identity association을 만든 직후 rollout한 Pod에는 <strong>변수가 주입되지 않아</strong> 노드 역할을 그대로 썼습니다. association도 eventual consistency입니다. <code>kubectl create --dry-run=server</code>로 주입을 확인한 뒤 rollout하자 해결됐습니다.<br>
4) 두 토큰은 같은 클러스터 issuer가 서명한 ServiceAccount 토큰이고 <strong><code>aud</code>만 다릅니다</strong>(<code>sts.amazonaws.com</code> 대 <code>pods.eks.amazonaws.com</code>). 받는 쪽이 STS냐 EKS Auth냐가 구조 차이의 출발점입니다.<br>
5) IMDS hop limit을 1로 내리자 레거시 복사본은 자격 증명을 아예 받지 못했습니다. 관리형 노드 그룹 교체에 878초가 걸려, 라이브로 보여 주기에는 깁니다.</div>

<nav class="toc" markdown="1">

**목차**

1. [왜 노드 역할에서 옮겨야 하나](#why)
2. [자격 증명은 어디서 오나](#chain)
3. [IRSA는 어떻게 동작하나](#irsa)
4. [Pod Identity는 어떻게 동작하나](#podid)
5. [두 방식 비교](#compare)
6. [라이브 데모: 같은 앱 세 벌](#demo)
7. [함정 1: association은 바로 반영되지 않습니다](#pitfall-assoc)
8. [함정 2: IAM 회수도 바로 반영되지 않습니다](#pitfall-iam)
9. [마지막 단계: IMDS 경로 닫기](#harden)
10. [운영 클러스터 이전 순서](#order)
11. [이번 측정의 한계](#limits)
12. [자료](#more)

</nav>

<h2 id="why">1. 왜 노드 역할에서 옮겨야 하나</h2>

노드 IAM 역할은 EC2 인스턴스 프로파일로 노드 전체에 붙습니다. 그 노드에서 IMDS(인스턴스 메타데이터 서비스)에 닿을 수 있는 Pod라면 누구든 노드 역할 자격 증명을 받습니다. 관리형 노드 그룹은 IMDS 응답 hop limit을 2로 두는 경우가 많아서, hostNetwork를 쓰지 않는 평범한 Pod도 여기에 해당합니다. 이번 데모의 레거시 복사본도 hostNetwork 없는 일반 Pod였는데 노드 역할을 문제없이 받았습니다.

그래서 결제 Pod 하나를 위해 붙인 권한이 같은 노드의 로그 수집기, 배치 Job, 다른 팀의 Pod에도 열립니다. CloudTrail에는 모두 같은 노드 인스턴스 세션으로 남아 누가 호출했는지 구분하기도 어렵습니다. 최소 권한과 감사 두 가지 이유로 권한을 워크로드 단위로 옮겨야 합니다.

<h2 id="chain">2. 자격 증명은 어디서 오나</h2>

<figure class="diagram"><a href="/assets/eks-wid-anim-0.svg"><img src="/assets/eks-wid-anim-0.svg" alt="세 Pod가 자격 증명을 받는 경로. 노드 역할 Pod는 IMDSv2에서, IRSA Pod는 STS AssumeRoleWithWebIdentity에서, Pod Identity Pod는 같은 노드의 Pod Identity Agent와 EKS Auth를 거쳐 임시 자격 증명을 받고, 각자 받은 자격 증명으로 S3 요청에 서명한다." loading="lazy"></a><figcaption>세 갈래 경로. 파란 봉투는 자격 증명 요청, 초록 열쇠는 돌아오는 임시 자격 증명입니다.</figcaption></figure>

AWS SDK와 CLI는 [기본 자격 증명 공급자 체인](https://docs.aws.amazon.com/sdkref/latest/guide/standardized-credentials.html)을 위에서부터 확인합니다. 환경 변수의 정적 키, 웹 ID 토큰(IRSA), 컨테이너 자격 증명 공급자(Pod Identity)가 IMDS보다 앞에 있습니다. 그래서 EKS가 Pod를 만들 때 IRSA나 Pod Identity용 변수를 넣어 주면, 같은 이미지의 같은 코드가 새 역할을 씁니다. 아무것도 넣지 않으면 체인 끝의 IMDS까지 내려가 노드 역할을 받습니다. 이번 데모의 세 복사본은 같은 이미지와 같은 스크립트이고, ServiceAccount 설정만 다릅니다.

<h2 id="irsa">3. IRSA는 어떻게 동작하나</h2>

<figure class="diagram"><a href="/assets/eks-wid-anim-1.svg"><img src="/assets/eks-wid-anim-1.svg" alt="IRSA 흐름. ServiceAccount 주석을 본 웹훅이 환경 변수와 projected 토큰을 Pod에 넣고, SDK가 토큰을 STS AssumeRoleWithWebIdentity로 보내면 STS가 IAM OIDC provider로 서명을 검증하고 신뢰 정책의 aud와 sub를 확인한 뒤 임시 자격 증명을 돌려준다." loading="lazy"></a><figcaption>IRSA. Pod가 STS와 직접 대화하고, IAM은 클러스터 OIDC issuer를 믿습니다.</figcaption></figure>

1. **신뢰 준비.** 클러스터의 OIDC(OpenID Connect) issuer를 계정의 IAM OIDC provider로 등록합니다. 역할의 신뢰 정책은 이 provider를 `Federated` principal로 두고 `aud`(`sts.amazonaws.com`)와 `sub`(`system:serviceaccount:<namespace>:<serviceaccount>`)를 조건으로 겁니다.
2. **Pod 생성 시 주입.** ServiceAccount에 `eks.amazonaws.com/role-arn` 주석이 있으면 EKS의 mutating 웹훅이 새 Pod에 `AWS_ROLE_ARN`, `AWS_WEB_IDENTITY_TOKEN_FILE`, projected 토큰 볼륨을 추가합니다. 실행 중인 Pod는 바뀌지 않으니 rollout이 필요합니다.
3. **토큰 교환.** SDK가 토큰을 리전 STS(Security Token Service) `AssumeRoleWithWebIdentity`로 보냅니다. STS는 issuer의 공개 키로 서명을 검증하고, `aud`·`sub` 조건을 확인한 뒤 임시 자격 증명을 돌려줍니다.

데모에서 꺼내 본 IRSA 토큰의 클레임은 `aud ["sts.amazonaws.com"]`, `sub system:serviceaccount:wid-d1124:orders-irsa`, 수명 86,400초(24시간)였습니다. 주입된 변수는 `AWS_ROLE_ARN`, `AWS_WEB_IDENTITY_TOKEN_FILE`, `AWS_STS_REGIONAL_ENDPOINTS` 세 개였습니다.

<h2 id="podid">4. Pod Identity는 어떻게 동작하나</h2>

<figure class="diagram"><a href="/assets/eks-wid-anim-2.svg"><img src="/assets/eks-wid-anim-2.svg" alt="Pod Identity 흐름. EKS API의 association이 ServiceAccount를 역할에 연결하고, EKS가 컨테이너 자격 증명 변수와 projected 토큰을 Pod에 넣는다. SDK는 같은 노드의 Pod Identity Agent를 호출하고, Agent가 노드 역할의 eks-auth 권한으로 EKS Auth를 호출하면 EKS가 세션 태그를 붙인 역할 자격 증명을 돌려준다." loading="lazy"></a><figcaption>Pod Identity. Pod는 같은 노드의 Agent에게 묻고, Agent가 EKS Auth에서 역할을 받아 옵니다.</figcaption></figure>

1. **에이전트.** 클러스터마다 `eks-pod-identity-agent` 애드온을 한 번 설치합니다. 노드의 hostNetwork에서 링크 로컬 주소 `169.254.170.23`을 쓰는 DaemonSet입니다. Auto Mode에는 내장되어 있습니다.
2. **역할과 association.** 역할은 서비스 principal `pods.eks.amazonaws.com` 하나만 신뢰합니다(`sts:AssumeRole`, `sts:TagSession`). 연결은 Kubernetes 주석이 아니라 EKS API의 association(클러스터·namespace·ServiceAccount → 역할)입니다.
3. **조회.** EKS가 새 Pod에 `AWS_CONTAINER_CREDENTIALS_FULL_URI=http://169.254.170.23/v1/credentials`와 토큰 파일 경로를 넣습니다. SDK가 토큰을 에이전트에 보내면, 에이전트가 노드 역할의 `eks-auth:AssumeRoleForPodIdentity` 권한으로 EKS Auth를 부르고, EKS Auth가 association을 확인해 세션 태그(`eks-cluster-arn`, `kubernetes-namespace`, `kubernetes-service-account` 등)를 붙인 자격 증명을 돌려줍니다.

여기서 헷갈리기 쉬운 점은 노드 역할이 여전히 등장한다는 것입니다. 노드 역할에 남는 것은 에이전트가 EKS Auth를 부를 권한(`AmazonEKSWorkerNodePolicy`에 포함)뿐이고, 앱의 S3 권한은 Pod Identity 역할에 있습니다. 데모의 Pod Identity 토큰은 IRSA 토큰과 같은 issuer, `aud ["pods.eks.amazonaws.com"]`, 수명은 약 22~24시간이었습니다(웹훅이 수명에 무작위 편차를 둬서 실측 79,636초와 86,171초).

<h2 id="compare">5. 두 방식 비교</h2>

AWS는 가능하면 EKS Pod Identity를 쓰라고 권장합니다([비교 문서](https://docs.aws.amazon.com/eks/latest/userguide/service-accounts.html)). 실제로 써 보니 차이는 "누가 무엇을 믿는가"에서 갈렸습니다.

| 항목 | IRSA | EKS Pod Identity |
| --- | --- | --- |
| 클러스터 쪽 준비 | 클러스터마다 IAM OIDC provider | 클러스터마다 Agent 애드온 (Auto Mode 내장) |
| 연결 위치 | ServiceAccount 주석 | EKS API association |
| 신뢰 정책 | 클러스터 OIDC provider + `aud`/`sub`. 클러스터가 늘면 문장 추가 | `pods.eks.amazonaws.com` 하나. 클러스터가 늘어도 그대로 |
| 확장 한도 | 계정당 OIDC provider 기본 100개, 신뢰 정책 기본 2,048자(역할당 연결 4개 안팎) | 클러스터당 association 5,000개 |
| 호출 경로 | Pod → 리전 STS | Pod → 노드 Agent → EKS Auth |
| 세션 태그 | 없음 | 클러스터·namespace·ServiceAccount 태그로 ABAC(속성 기반 접근 제어) |
| 실행 환경 | EC2·Fargate, EKS Anywhere, 자체 관리 Kubernetes | EKS의 Linux EC2 노드만 |

새로 만드는 EKS EC2 워크로드라면 Pod Identity가 단순합니다. 역할 하나를 여러 클러스터에서 재사용할 때 신뢰 정책을 건드리지 않아도 되고, IAM 담당과 클러스터 담당의 책임이 깔끔하게 나뉩니다. Fargate나 Windows 노드, EKS 밖 Kubernetes와 같은 구성을 써야 한다면 IRSA가 답입니다. 두 방식은 한 클러스터에서 같이 쓸 수 있습니다. 한 ServiceAccount에 IRSA 주석과 association을 모두 두면 어떻게 될지 데모 클러스터에서 `kubectl create --dry-run=server`로 확인해 봤습니다. 주석만 있을 때는 IRSA 변수(`AWS_ROLE_ARN`, `AWS_WEB_IDENTITY_TOKEN_FILE`)가, association을 더하자 Pod Identity 변수만 주입됐습니다. SDK 체인에서는 웹 ID가 앞이지만, 그 전에 EKS 웹훅이 association을 우선해 IRSA 변수를 아예 넣지 않습니다. 다만 association을 추가하기 전에 떠 있던 Pod는 다시 만들기 전까지 IRSA를 계속 쓰므로, 방식을 바꿀 때는 rollout하고 쓰지 않는 주석은 지웁니다.

<h2 id="demo">6. 라이브 데모: 같은 앱 세 벌</h2>

<figure class="diagram"><a href="/assets/eks-wid-anim-3.svg"><img src="/assets/eks-wid-anim-3.svg" alt="데모 흐름. 처음에는 orders 앱 세 벌이 모두 IMDS에서 노드 역할을 받아 S3를 읽는다. migrate-irsa 뒤 한 벌은 IRSA 역할을, migrate-podid 뒤 다른 한 벌은 Pod Identity 역할을 받는다. revoke로 노드 역할의 S3 권한을 지우면 레거시 한 벌만 AccessDenied가 된다." loading="lazy"></a><figcaption>데모 순서. 네 단계 모두 같은 클러스터, 같은 노드, 같은 S3 객체입니다.</figcaption></figure>

데모 스크립트(`demo.sh`)가 기존 클러스터에 만드는 리소스는 이름에 run ID가 들어가거나 run 태그가 붙고, 스크립트는 그 이름이나 태그에 해당하는 것만 수정하고 지웁니다. VPC나 보안 그룹은 만들지 않고, 인바운드 포트도 열지 않습니다. 두 번째 터미널에 `watch`를 띄워 두면 세 복사본의 상태가 2초마다 바뀝니다. 아래는 마지막 `revoke` 직후의 실제 화면입니다.

```text
COPY            CREDENTIALS    ROLE                           S3        AT
orders-legacy   node (IMDS)    wid-d1124-node  ← node       denied    04:12:56
orders-irsa     IRSA (STS)     wid-d1124-irsa                 allowed   04:12:58
orders-podid    Pod Identity   wid-d1124-podid                allowed   04:12:58
```

| 단계 | 명령 | orders-legacy | orders-irsa | orders-podid | 걸린 시간 |
| --- | --- | --- | --- | --- | --- |
| 시작 | `up` | 노드 역할 · 허용 | 노드 역할 · 허용 | 노드 역할 · 허용 | 약 5분 (노드 그룹 생성) |
| IRSA로 | `migrate-irsa` | 그대로 | IRSA 역할 · 허용 | 그대로 | 6초 |
| Pod Identity로 | `migrate-podid` | 그대로 | 그대로 | Pod Identity 역할 · 허용 | 11초 (주입 확인 5초 포함) |
| 노드 권한 회수 | `revoke` | 노드 역할 · **거부** | 허용 | 허용 | 13초 뒤 거부 |
| IMDS 차단 (선택) | `harden` | **자격 증명 없음** | 허용 | 허용 | 878초 (첫 리허설) |

`diff` 명령은 두 방식의 차이를 한 화면에 모읍니다. 주입된 변수 이름, 토큰 클레임(토큰 자체는 출력하지 않습니다), 두 역할의 신뢰 정책 principal과 조건 키를 나란히 보여 줍니다. 발표에서는 이 화면이 "결과는 같은데 배선이 다르다"를 설명하는 데 가장 효과적이었습니다.

<h2 id="pitfall-assoc">7. 함정 1: association은 바로 반영되지 않습니다</h2>

첫 리허설에서 association을 만들고 곧바로 rollout했더니, 새 Pod에 Pod Identity 변수가 하나도 없었습니다. 그 Pod는 체인 끝까지 내려가 노드 역할을 그대로 썼고, 이어서 노드 권한을 회수하자 Pod Identity로 옮겼다고 생각한 복사본까지 거부됐습니다. AWS 문서도 association이 몇 초 늦게 반영될 수 있다고 적고 있습니다.

해결은 rollout 전에 주입을 확인하는 것입니다. `kubectl create --dry-run=server`로 보낸 요청도 mutating 웹훅을 거치므로, Deployment의 Pod 템플릿을 dry-run으로 보내 응답에 `AWS_CONTAINER_CREDENTIALS_FULL_URI`가 붙는지 봅니다. 붙으면 rollout합니다. 이렇게 바꾼 두 번째 리허설에서는 association을 만든 지 5초가 지나서야 주입이 확인됐고, 그 뒤 rollout한 Pod는 정상으로 Pod Identity 역할을 썼습니다. 운영 배포 파이프라인에서도 association 생성과 rollout 사이에 같은 확인을 넣어 두면 안전합니다.

<h2 id="pitfall-iam">8. 함정 2: IAM 회수도 바로 반영되지 않습니다</h2>

노드 역할의 인라인 정책을 지운 뒤 레거시 복사본은 13초 만에 `AccessDenied`가 됐습니다. 그런데 계속 지켜보니 약 4분 30초 뒤에 허용 응답이 두 번 더 섞여 나왔고, 그 직후 다시 확인한 1분 동안(로그 11건)은 모두 거부였습니다. IAM 변경이 모든 서비스 엔드포인트에 퍼지는 데 시간이 걸린다는 뜻입니다. 운영에서 "권한을 지웠는데 아무 일도 없었다"는 판단은 수 분, 크론 주기까지 고려하면 더 길게 관찰한 뒤에 내려야 합니다. 반대로 원복이 필요할 때도 정책 복구 후 전파를 확인하고 이전 Pod를 배포합니다.

<h2 id="harden">9. 마지막 단계: IMDS 경로 닫기</h2>

권한을 옮긴 뒤에도 일반 Pod가 IMDS에 닿을 수 있으면, 노드 역할에 남은 권한(노드 등록, ECR pull, CNI, EKS Auth 호출)은 여전히 Pod에서 쓸 수 있습니다. 데모의 `harden`은 launch template 새 버전에 `HttpPutResponseHopLimit=1`을 넣고 노드 그룹을 교체합니다. 교체 후 레거시 복사본은 `no-credentials`가 되어 아무 역할도 받지 못했고, IRSA와 Pod Identity 복사본은 영향이 없었습니다. Pod는 IMDS 대신 같은 노드의 Agent(169.254.170.23)에 묻고, Agent는 hostNetwork에서 돌기 때문에 hop limit 1에서도 IMDS로 노드 역할 자격 증명을 받아 EKS Auth를 부릅니다. 그래서 IMDS 자체를 끄면(`HttpEndpoint=disabled`) Agent도 멈춥니다. 닫아야 하는 것은 일반 Pod의 경로이지 IMDS 전체가 아닙니다.

관리형 노드 그룹 교체(새 노드 생성, drain, 종료)에 878초가 걸렸습니다. 그래서 발표에서는 `revoke` 직후에 시작만 해 두고, 결과는 리허설 화면으로 설명합니다. 미리 실행해 두면 첫 장면부터 레거시가 자격 증명을 못 받는 상태가 되어 버립니다. Karpenter는 `EC2NodeClass.spec.metadataOptions`, Auto Mode는 기본값이 hop limit 1입니다. hostNetwork Pod는 hop limit과 관계없이 IMDS에 닿으므로 별도로 통제해야 합니다.

<h2 id="order">10. 운영 클러스터 이전 순서</h2>

1. **의존성 조사.** 노드 역할 정책과 CloudTrail의 실제 세션을 비교해, 어떤 워크로드가 어떤 API를 쓰는지 찾습니다. 노드 등록·ECR·CNI에 필요한 정책은 남깁니다.
2. **워크로드별 역할과 ServiceAccount.** 리소스 단위로 권한을 좁힌 역할을 만들고, IRSA면 OIDC provider와 `aud`/`sub` 신뢰, Pod Identity면 Agent와 association을 준비합니다. SDK 최소 버전도 확인합니다.
3. **카나리.** 새 Pod에서 주입된 변수 이름, 호출자 ARN, 필요한 API 성공을 확인합니다. Pod Identity는 주입을 먼저 확인합니다(함정 1).
4. **순차 전환과 회수.** 워크로드가 노드 역할에 의존하지 않는 것을 확인한 뒤 해당 정책 문장만 회수하고, 수 분 이상 관찰합니다(함정 2). 롤백용 정책 복원본을 준비합니다.
5. **IMDS 제한.** 호환성을 확인한 노드 그룹부터 hop limit 1로 교체합니다.

<h2 id="limits">11. 이번 측정의 한계</h2>

- **복사본마다 Pod 1개, 노드 1대였습니다.** 수백 Pod에서의 STS 할당량 사용량이나 Agent 부하는 재지 않았습니다.
- **전환 시간(6초, 11초)은 rollout 시간이 대부분이고, 리허설은 두 번뿐입니다.** 자격 증명 조회 자체의 지연은 따로 재지 않았습니다.
- **eventual consistency는 리허설 몇 번에서 관찰한 값입니다.** 13초와 4분 30초가 늘 같다고 볼 수는 없습니다.
- **교차 계정, Fargate, 프라이빗 클러스터(STS·EKS Auth VPC 엔드포인트)는 시험하지 않았습니다.**

<h2 id="more">12. 자료</h2>

- 데모 코드와 발표 런북: [kubernetes-docs/examples/security/eks-workload-identity](https://github.com/Atom-oh/kubernetes-docs/tree/main/examples/security/eks-workload-identity)
- 교재(한국어·영어, 퀴즈 포함): [EKS 노드 IAM 권한을 IRSA와 Pod Identity로 이전하기](https://www.atomai.click/kubernetes-docs/ko/security/17-eks-workload-identity-migration)
- 발표 자료: [EKS 워크로드 IAM 이전](https://presentation.atomai.click/eks-workload-identity/)
- [IAM roles for service accounts](https://docs.aws.amazon.com/eks/latest/userguide/iam-roles-for-service-accounts.html)
- [Learn how EKS Pod Identity grants pods access to AWS services](https://docs.aws.amazon.com/eks/latest/userguide/pod-identities.html)
- [Understand how EKS Pod Identity works](https://docs.aws.amazon.com/eks/latest/userguide/pod-id-how-it-works.html)
- [EKS Best Practices: Identity and Access Management](https://docs.aws.amazon.com/eks/latest/best-practices/identity-and-access-management.html)

데모 노드 그룹과 역할, 버킷은 발표가 끝난 뒤 `demo.sh down`으로 삭제합니다.
