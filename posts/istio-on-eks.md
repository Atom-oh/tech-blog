---
title: "Amazon EKS에서 Istio 제대로 쓰기: 도입 판단부터 sidecar와 ambient, 트래픽, 보안, 운영 함정까지"
date: 2026-10-20
description: "EKS에 Istio를 도입할지 판단하는 기준, sidecar와 ambient 선택, 트래픽 관리와 mTLS 전환, 복원력 설정, AWS 통합 지점과 운영에서 자주 막히는 곳을 한 번에 정리한 가이드"
tags: istio, service-mesh, eks
---

# Amazon EKS에서 Istio 제대로 쓰기: 도입 판단부터 sidecar와 ambient, 트래픽, 보안, 운영 함정까지

<p class="lead">Istio 문서는 양이 많습니다. 설치, 트래픽 관리, 보안, 복원력, 관찰성, 고급 기능까지 장마다 수백 줄의 YAML이 이어지고, 처음 보는 사람은 어디부터 읽어야 할지 막막합니다. 이 글은 EKS 환경을 기준으로 정리해 둔 Istio 문서 세트를 한 편의 글로 다시 엮은 것입니다. 모든 장을 요약하지는 않고, 실제로 도입을 검토하고 운영할 때 판단이 갈리는 지점만 골랐습니다. 이 글은 개념과 설정을 설명하는 가이드이며 별도로 성능이나 비용을 측정한 결과는 담고 있지 않습니다. 성능과 비용은 각자의 워크로드에서 직접 확인해야 합니다.</p>

<div class="callout"><strong>기준 버전</strong>2026년 10월 검토 기준입니다. Istio 1.31의 최신 patch는 1.31.2이고, Istio 1.31이 공식 지원하는 Kubernetes 범위는 1.32~1.37입니다. 설치 직전에는 Istio 지원 매트릭스와 EKS 버전 수명 주기를 다시 확인하세요.</div>

<nav class="toc" markdown="1">

**목차**

1. [서비스 메시가 정말 필요한가](#why)
2. [Istio 구조 한눈에 보기](#arch)
3. [EKS에 설치할 때](#install)
4. [트래픽 관리의 핵심](#traffic)
5. [보안: mTLS 전환과 인가 정책](#security)
6. [복원력: outlier detection, circuit breaking, locality](#resilience)
7. [AWS 통합 지점](#aws)
8. [운영에서 자주 막히는 곳](#ops)
9. [Istio와 대안 비교](#alternatives)
10. [더 읽기](#more)

</nav>

<h2 id="why">1. 서비스 메시가 정말 필요한가</h2>

![Istio 서비스 메시 개념도](https://istio.io/latest/img/service-mesh.svg)

서비스 메시는 마이크로서비스 사이의 통신을 애플리케이션 밖의 인프라 계층에서 다룹니다. Istio를 넣으면 코드 수정 없이 트래픽 라우팅, mTLS와 인가, 메트릭과 로그, retry와 circuit breaking 같은 기능을 얻습니다. 다만 공짜는 아닙니다. 프록시가 추가되고, control plane을 운영해야 하고, 장애가 났을 때 봐야 할 계층이 하나 늘어납니다. 그래서 문서 세트도 "필요한가"부터 묻습니다.

### 메시가 도움이 되는 상황

- 서비스 간(East-West) 호출이 많고, 여러 팀이 서로 다른 언어로 독립적으로 서비스를 만드는 환경
- 서비스 간 암호화와 워크로드 신원 기반 접근 제어(Zero Trust)가 요구되는 환경. 서비스마다 보안 로직과 인증서 관리를 따로 구현하면 정책이 일관되지 않기 쉽습니다.
- Canary 배포, 헤더 기반 라우팅, traffic mirroring, fault injection 같은 L7 트래픽 제어가 필요한 경우
- 코드 수정 없이 일관된 형식의 메트릭과 서비스 토폴로지를 보고 싶은 경우

### 메시가 오히려 부담인 상황

- 모놀리식 애플리케이션 하나와 DB 정도의 단순한 구조. Ingress 컨트롤러나 Gateway API 구현, 로드 밸런서로 충분합니다.
- 서비스 수가 적고 L3/L4 격리만 필요한 경우. CNI가 지원하면 NetworkPolicy로 격리할 수 있습니다. 단, NetworkPolicy는 mTLS나 HTTP 수준 인가를 제공하지 않습니다.
- Istio와 Envoy를 이해하고 업그레이드, 패치, 디버깅을 감당할 인력이 없는 경우
- 지연과 리소스에 극도로 민감한 경우. 이때는 실제 트래픽과 설정으로 오버헤드를 측정해 보고, ambient 모드나 Cilium 같은 CNI 기반 대안도 함께 검토합니다.

<div class="callout warn"><strong>숫자는 예시일 뿐입니다</strong>문서 세트의 의사결정 체크리스트에는 "마이크로서비스 10개 이상" 같은 기준이 나오지만, 이는 논의를 시작하기 위한 예시이지 Istio의 요구사항이 아닙니다. 서비스가 몇 개 안 되어도 보안 요구 때문에 메시가 필요할 수 있고, 반대로 큰 시스템이 이미 다른 계층에서 필요한 제어를 갖추고 있을 수도 있습니다. 서비스 개수보다 "아직 충족하지 못한 요구사항이 무엇인가"를 먼저 따져 보세요.</div>

### 도입한다면 점진적으로

도입하기로 했다면 한 번에 모든 기능을 켜지 않는 것이 핵심입니다. 문서 세트가 권하는 순서는 다음과 같습니다.

1. 1~2개 네임스페이스로 pilot 프로젝트를 시작합니다.
2. 관찰성을 먼저 확보합니다(메트릭, 로그, 추적).
3. mTLS를 PERMISSIVE로 시작해 STRICT로 전환합니다.
4. VirtualService와 DestinationRule로 트래픽 관리를 적용합니다.
5. 검증이 끝나면 전사로 확대합니다.

"Day 1에 모든 기능 활성화", "PERMISSIVE 모드를 장기간 방치", "모니터링 없이 프로덕션 배포"는 문서가 꼽는 대표적인 안티패턴입니다.

<h2 id="arch">2. Istio 구조 한눈에 보기</h2>

![Istio 아키텍처](https://istio.io/latest/docs/ops/deployment/architecture/arch.svg)

Istio는 control plane과 data plane으로 나뉩니다.

**Control plane (istiod)** 은 하나의 바이너리입니다. 예전에는 Pilot(서비스 디스커버리와 프록시 구성), Citadel(인증서 발급), Galley(구성 검증)가 별도 컴포넌트였지만 Istio 1.5부터 istiod 내부 기능으로 통합되었습니다. 지금 이 이름들은 기능을 설명하는 역사적 명칭일 뿐입니다. istiod는 Kubernetes Service, EndpointSlice, Pod 상태, ServiceEntry를 감시하고, 사용자가 작성한 Istio 리소스를 Envoy 구성으로 변환해 내려보냅니다.

**Data plane** 은 실제 트래픽을 처리합니다. 여기서 두 가지 모드가 갈립니다.

### xDS: istiod와 Envoy가 대화하는 방식

istiod가 Envoy에 구성을 내려보내는 프로토콜이 xDS입니다. 이름만 알아 두면 디버깅할 때 훨씬 편합니다.

| API | 역할 | 대응하는 Istio 개념 |
|---|---|---|
| LDS (Listener) | 수신 포트 구성 | 15001, 15006 같은 리스너 |
| RDS (Route) | HTTP 라우팅 규칙 | VirtualService |
| CDS (Cluster) | 업스트림 서비스 | DestinationRule |
| EDS (Endpoint) | 파드 IP 목록 | Service Endpoints |
| SDS (Secret) | TLS 인증서 | mTLS 인증서 |

LDS, RDS, CDS, EDS는 보통 하나의 ADS 스트림을 공유합니다. sidecar의 SDS는 istiod와 맺는 다섯 번째 스트림이 아니라 파드 안의 Istio agent가 제공합니다. `istioctl proxy-config listeners|routes|clusters|endpoints` 명령이 바로 이 네 가지를 들여다보는 도구입니다.

한 가지 기억할 점은, 기본적으로 각 Envoy가 메시 전체의 서비스 정보를 받는다는 것입니다. 실제로는 서비스 두세 개만 호출하는 파드도 메시의 모든 서비스 구성을 들고 있게 됩니다. `Sidecar` 리소스로 가져올 호스트 범위를 좁히면 구성 크기와 프록시 메모리, 푸시 작업량을 줄일 수 있습니다(절감 폭은 실제 환경에서 확인해야 합니다). 이것은 구성 범위 제어일 뿐 egress 방화벽이 아니라는 점도 함께 기억하세요.

### Sidecar 모드와 ambient 모드

**Sidecar 모드** 는 등록된 파드마다 Envoy 컨테이너를 주입합니다. iptables(또는 Istio CNI)로 파드의 트래픽을 가로채 Envoy를 거치게 합니다. 성숙하고 파드 단위로 세밀하게 제어할 수 있지만, 파드마다 프록시 리소스가 들고, 프록시를 추가하거나 제거하려면 파드를 다시 만들어야 하며, 앱과 프록시의 시작과 종료 순서를 신경 써야 합니다.

**Ambient 모드** 는 sidecar 없이 두 계층으로 나눕니다. Istio 1.24에서 core 기능이 GA가 되었습니다.

- **ztunnel**: Rust로 작성된 노드 단위 L4 프록시로, DaemonSet으로 실행됩니다. 등록된 워크로드의 트래픽을 Istio CNI가 파드 내부 리다이렉션으로 가로채고, ztunnel이 mTLS와 L4 텔레메트리, L4 로드 밸런싱을 처리합니다. 노드 사이 전송에는 HTTP/2 기반 터널 프로토콜인 HBONE을 쓰며 보통 TCP 15008 포트를 사용합니다.
- **waypoint**: L7 기능이 필요할 때만 배치하는 Envoy 기반 프록시입니다. 네임스페이스, Service, Pod 단위로 등록해 여러 워크로드가 공유합니다.

```yaml
apiVersion: gateway.networking.k8s.io/v1
kind: Gateway
metadata:
  name: reviews-waypoint
  namespace: ambient-demo
  labels:
    istio.io/waypoint-for: service
spec:
  gatewayClassName: istio-waypoint
  listeners:
  - name: mesh
    port: 15008
    protocol: HBONE
```

워크로드를 ambient에 등록할 때는 네임스페이스에 `istio.io/dataplane-mode=ambient` 레이블을 붙이고, waypoint를 쓰려면 Namespace, Service 또는 Pod에 `istio.io/use-waypoint` 레이블을 붙입니다. ServiceAccount에 레이블을 붙여도 waypoint가 선택되지 않는다는 점은 예전 preview 자료와 다른 부분이라 자주 헷갈립니다.

### 어떤 모드를 고를까

| 판단 기준 | Sidecar 쪽이 맞는 경우 | Ambient 쪽이 맞는 경우 |
|---|---|---|
| 필요한 기능 | VM 통합처럼 sidecar에서만 지원되는 기능이 필요할 때 | 대부분 L4(mTLS, 기본 텔레메트리)로 충분하고 일부만 L7이 필요할 때 |
| 제어 단위 | 파드별 세밀한 정책, 파드별 프록시 버전 관리 | 공유 프록시로 운영 범위를 줄이고 싶을 때 |
| 등록과 롤아웃 | 파드 재생성을 감수할 수 있을 때 | 기존 파드를 재시작하지 않고 메시에 등록하고 싶을 때 |
| 성숙도 | 오래 검증된 구성을 원할 때 | core GA 이후 기능별 지원 상태를 확인할 수 있을 때 |

ambient에서 circuit breaker, retry와 timeout, 헤더 조작 같은 L7 기능은 waypoint가 있어야 동작합니다. core GA가 모든 API의 GA를 뜻하지도 않습니다. 현재 문서는 ambient에서의 VirtualService를 Alpha로 설명하므로, waypoint 라우팅에는 Gateway API의 HTTPRoute를 쓰고 둘을 섞지 않는 편이 안전합니다. 리소스 절감 효과 역시 노드 수, waypoint replica 수, 같은 정책 조건에서 측정해야 판단할 수 있습니다.

<h2 id="install">3. EKS에 설치할 때</h2>

### 버전부터 맞춥니다

2026년 10월 검토 기준으로 Istio 1.31.2가 최신 patch이며 [ISTIO-SECURITY-2026-007](https://istio.io/latest/news/security/istio-security-2026-007/) 보안 수정을 포함합니다. 그 앞의 1.31.1은 `BackendTLSPolicy`의 CA 참조가 해석되지 않을 때 평문으로 fallback하던 문제를 고쳤습니다. 신규 설치라면 1.31.2를 쓰는 것이 좋습니다.

Istio 1.31의 Kubernetes 지원 범위는 1.32~1.37입니다. 검토 시점에 EKS 표준 지원은 1.34~1.37이었으므로 이 범위가 가장 안전한 교집합입니다. EKS 연장 지원 버전 중 1.32와 1.33도 Istio 1.31 지원 범위에는 들어갑니다. 지원 상태는 계속 바뀌니 설치 전에 [Istio 지원 매트릭스](https://istio.io/latest/docs/releases/supported-releases/)와 [EKS 버전 수명 주기](https://docs.aws.amazon.com/eks/latest/userguide/kubernetes-versions.html)를 다시 확인하세요.

### 노드와 플랫폼 조건

- 문서 세트의 예제는 Linux EC2 워커 노드를 대상으로 합니다. Fargate는 Istio CNI나 ztunnel 같은 DaemonSet과 특권 네트워킹을 실행할 수 없습니다.
- EKS Auto Mode는 네트워킹과 로드 밸런서 관리 방식이 달라 별도의 사전 검증이 필요합니다.
- ambient 모드에서 VPC CNI의 Pod ENI trunking과 SecurityGroupPolicy를 함께 쓴다면, strict 모드가 link-local 헬스 프로브를 막을 수 있습니다. [EKS ambient 사전 요구사항](https://istio.io/latest/docs/ambient/install/platform-prerequisites/#amazon-elastic-kubernetes-service-eks)의 standard enforcing mode 또는 exec probe 대안을 검토하세요.

### istioctl 또는 Helm, 하나만

설치 방법은 istioctl과 Helm 중 하나를 고릅니다. 같은 control plane에 두 방식을 섞어 쓰지 마세요. 업스트림 in-cluster operator는 1.23에서 사용 중단되고 1.24에서 제거되었습니다. IstioOperator 형식의 파일은 `istioctl install -f`의 입력으로만 계속 쓸 수 있습니다.

Istio 1.31부터 Helm 차트는 `https://blob.istio.io/istio-release/charts` 저장소를 씁니다. 기존 Google 호스팅 저장소에는 새 릴리스가 올라오지 않으니, 오래된 파이프라인이 있다면 저장소 주소부터 바꿔야 합니다.

```bash
helm repo add istio https://blob.istio.io/istio-release/charts
helm repo update

kubectl create namespace istio-system
helm install istio-base istio/base -n istio-system \
  --set defaultRevision=default --version 1.31.2
helm install istiod istio/istiod -n istio-system \
  --version 1.31.2 --wait
```

istiod 1.31.0과 1.31.2 차트 아카이브에는 `eks` 플랫폼 프로필 파일이 없습니다(문서 세트가 2026년 10월 16일에 확인). 그래서 `global.platform=eks`가 있다고 가정하지 말고, EKS 사전 요구사항과 로드 밸런서 설정을 명시적으로 적용하는 편이 낫습니다. 어느 방식이든 적용 전에 렌더링 결과를 직접 검토하세요.

설치 후에는 `istioctl analyze -A`로 정적 구성을 점검하고, `istioctl proxy-status`로 각 프록시가 istiod와 동기화되었는지 확인하는 습관을 들이면 좋습니다.

<h2 id="traffic">4. 트래픽 관리의 핵심</h2>

### 세 가지 리소스의 역할 분담

- **Gateway**: 메시로 들어오는 진입점(호스트, 포트, 프로토콜, TLS)을 정의합니다. `networking.istio.io/v1`의 Gateway는 이미 떠 있는 게이트웨이 프록시를 구성할 뿐 프록시를 새로 만들지 않습니다.
- **VirtualService**: 요청을 어디로 보낼지(경로, 헤더, 가중치, retry, timeout) 정합니다. `gateways` 필드가 없으면 메시 내부 트래픽에 적용되고, 인바운드에 적용하려면 Gateway에 연결하고 호스트가 일치해야 합니다.
- **DestinationRule**: 목적지에 도착한 뒤의 정책(subset, 로드 밸런싱, connection pool, outlier detection, TLS)을 정합니다.

이 셋은 프록시가 읽는 API 객체이지 별도의 네트워크 홉이 아닙니다. 그리고 VirtualService가 참조하는 subset은 반드시 DestinationRule에 정의되어 있어야 하고, 그 subset 레이블과 일치하는 ready 파드가 있어야 합니다.

### Istio Gateway와 Kubernetes Gateway API

Istio Gateway(`networking.istio.io`)와 Kubernetes Gateway API(`gateway.networking.k8s.io`)는 이름이 비슷하지만 다른 리소스입니다. Istio는 Gateway API를 지원하며, istio.io는 앞으로 이를 트래픽 관리의 기본 API로 삼을 계획이라고 밝히고 있습니다([Gateway API 가이드](https://istio.io/latest/docs/tasks/traffic-management/ingress/gateway-api/)). Gateway API를 쓰면 Gateway 리소스를 기준으로 게이트웨이 Deployment와 Service가 자동으로 만들어집니다. 앞에서 본 것처럼 ambient의 waypoint도 Gateway API 리소스입니다.

새로 시작한다면 Gateway API를 우선 검토하고, 기존 Istio API 자산이 많다면 무리하게 옮기기보다 같은 호스트에 두 API를 섞어 쓰지 않도록 경계를 정하는 것이 현실적입니다. 장애를 볼 때도 확인할 상태가 다릅니다. Gateway API에서는 Gateway의 Accepted, Programmed와 HTTPRoute parent의 ResolvedRefs 같은 조건을 확인합니다.

### Canary와 트래픽 분할

가중치 기반 분할은 Istio를 쓰는 가장 흔한 이유 중 하나입니다.

```yaml
apiVersion: networking.istio.io/v1
kind: VirtualService
metadata:
  name: reviews
spec:
  hosts:
  - reviews
  http:
  - route:
    - destination:
        host: reviews
        subset: v1
      weight: 95
    - destination:
        host: reviews
        subset: v2
      weight: 5
```

문서 세트는 5%에서 시작해 10%, 25%, 50%, 100%로 모니터링하며 올리는 흐름을 예로 듭니다. 요청률, 에러율, P50/P95/P99 지연, 성공률을 보면서 단계를 넘기고, Argo Rollouts 같은 도구와 연동하면 분석과 승격을 자동화할 수 있습니다. 헤더 기반으로 개발자만 새 버전으로 보내는 패턴도 유용하지만, 클라이언트가 보내는 헤더는 라우팅 힌트일 뿐 인증이나 인가 경계가 아니라는 점은 분명히 해 두어야 합니다. Traffic mirroring 역시 90/10 분배의 나머지가 아니라 선택한 요청을 추가로 복제해 보내고 응답은 버리는 기능입니다.

### Retry와 timeout: 가장 많이 틀리는 곳

**첫째, `retries`를 생략해도 retry는 꺼지지 않습니다.** VirtualService 참조 문서에 따르면 별도 지정이 없을 때의 클러스터 기본 정책은 `attempts: 2`, `retryOn: connect-failure,refused-stream,unavailable,cancelled`입니다. 문서 세트는 1.31.0 구현이 여기에 `retriable-status-codes`도 함께 켠다고 정리해 두었습니다. `attempts`는 최초 요청 이후의 추가 재시도 횟수이므로 최대 전달 횟수는 3회입니다. 프록시 retry를 확실히 끄려면 해당 route에 `attempts: 0`을 명시해야 합니다.

**둘째, 비멱등 쓰기에는 mesh retry를 끕니다.** POST 요청이 서버에서 실제로 처리되고 응답만 유실되었다면, 프록시는 그 사실을 알 수 없어 같은 요청을 다시 보냅니다. `reset`, `503`, timeout은 서버가 요청을 처리하지 않았다는 증거가 아닙니다. 결제나 재고 차감이라면 그대로 중복 실행입니다. 그래서 메서드별로 route를 나눕니다.

```yaml
apiVersion: networking.istio.io/v1
kind: VirtualService
metadata:
  name: order-service
spec:
  hosts:
  - order-service
  http:
  - name: writes-no-mesh-retry
    match:
    - method:
        regex: "^(POST|PUT|PATCH|DELETE)$"
    route:
    - destination:
        host: order-service
    timeout: 10s
    retries:
      attempts: 0
  - name: reads-limited-retry
    match:
    - method:
        regex: "^(GET|HEAD)$"
    route:
    - destination:
        host: order-service
    timeout: 5s
    retries:
      attempts: 2
      perTryTimeout: 2s
      retryOn: connect-failure,refused-stream
```

PUT과 DELETE는 HTTP 명세상 멱등일 수 있지만, 애플리케이션 계약이 같은 요청의 반복 실행을 실제로 안전하게 처리할 때만 retry를 허용합니다. 쓰기 경로의 재시도는 Idempotency Key와 DB unique constraint를 같은 트랜잭션에서 다루는 식으로 애플리케이션이 책임지게 하는 것이 정석입니다. Circuit breaker를 함께 건다고 해서 비멱등 요청의 중복 실행이 막히지는 않습니다.

**셋째, retry는 계층마다 곱해집니다.** 호출 체인의 여러 계층이 각자 retry를 걸면 맨 아래 백엔드가 받는 시도 수는 곱셈으로 늘어납니다. 단순 계산 예로, 세 계층이 모두 최대 3회씩 전달하면 최하단은 하나의 사용자 요청에 대해 최대 27번 호출될 수 있습니다. 백엔드가 이미 과부하로 느려진 상황이라면 retry가 장애를 키웁니다. retry는 체인에서 한 곳(보통 호출자에 가까운 쪽)에만 두고, 상위 계층의 timeout이 하위 계층보다 길도록 맞추세요.

**넷째, `perTryTimeout`과 전체 `timeout`의 관계를 계산해 둡니다.** `attempts: 3`, `perTryTimeout: 3s`라면 이론상 상한은 `(1 + 3) × 3s = 12s`이지만 route의 `timeout: 10s`가 먼저 적용됩니다. 실제 재시도 횟수는 backoff(Envoy 기본은 25ms 기저 간격에 jitter가 있는 지수 방식)와 남은 전체 timeout에 따라 줄어듭니다. 스트리밍이나 장시간 gRPC 응답에는 짧은 HTTP timeout이 맞지 않을 수 있으니 따로 판단합니다.

<h2 id="security">5. 보안: mTLS 전환과 인가 정책</h2>

![Istio 보안 아키텍처](https://istio.io/latest/docs/concepts/security/arch-sec.svg)

Istio는 각 워크로드에 SPIFFE 형식의 신원(`spiffe://cluster.local/ns/<namespace>/sa/<serviceaccount>`)을 부여합니다. sidecar 모드에서는 파드 안의 Istio agent가 istiod에 CSR을 보내고, 발급받은 X.509 인증서를 SDS로 Envoy에 전달합니다. 기본 인증서 수명은 24시간이며 만료 전에 자동 갱신됩니다. 이 신원은 Kubernetes ServiceAccount에 묶여 있으므로, ServiceAccount를 서비스별로 잘게 나눌수록 인가 정책도 세밀해집니다.

### 자동 mTLS만으로는 부족합니다

Auto mTLS는 메시 피어끼리 통신할 때 알아서 mTLS를 선택합니다. 하지만 기본 PERMISSIVE 상태에서는 평문 요청도 받아들입니다. 즉 "암호화가 된다"와 "평문을 거부한다"는 다른 이야기입니다. 평문을 거부하려면 수신 측에 STRICT를 걸어야 합니다.

| 모드 | 동작 |
|---|---|
| STRICT | mTLS만 허용. 프로덕션 목표 상태 |
| PERMISSIVE | mTLS와 평문 모두 허용. 마이그레이션용 |
| DISABLE | sidecar의 mTLS 해제. ambient에서는 지원하지 않음 |

### PERMISSIVE에서 STRICT로 옮기는 순서

1. `kubectl get peerauthentication -A`로 현재 정책을 파악합니다.
2. PERMISSIVE 상태에서 수신 측의 `istio_requests_total`, `istio_tcp_connections_opened_total`을 `connection_security_policy` 레이블별로 조회해 평문으로 들어오는 호출자를 찾습니다. 메트릭에 안 보인다고 평문 호출자가 없다는 뜻은 아니므로 대표 요청을 직접 만들어 확인합니다.
3. 평문 호출자를 메시에 넣거나, 예외가 꼭 필요하면 최소 범위의 네임스페이스나 워크로드로 한정합니다.
4. 준비가 끝나면 STRICT로 전환합니다.

```yaml
apiVersion: security.istio.io/v1
kind: PeerAuthentication
metadata:
  name: default
  namespace: istio-system
spec:
  mtls:
    mode: STRICT
```

메시 루트 네임스페이스(기본 `istio-system`)에 selector 없이 만든 정책은 메시 전체에 적용됩니다. 처음에는 네임스페이스 단위로 전환하고 마지막에 메시 전체로 넓히는 편이 안전합니다. 또 하나, PeerAuthentication은 **수신** 측이 무엇을 허용할지 정하고, 송신 측 Envoy가 어떤 TLS를 쓸지는 DestinationRule의 `trafficPolicy.tls`가 정합니다. 누군가 DestinationRule에 `tls.mode: DISABLE`을 명시해 두었다면 목적지의 STRICT와 충돌해 연결이 실패합니다. 이 경우 의도하지 않은 override를 제거하거나 `ISTIO_MUTUAL`로 맞춥니다.

### AuthorizationPolicy: 기본 거부와 명시적 허용

인가의 출발점은 "기본 거부, 필요한 것만 허용"입니다. 빈 spec의 정책은 어떤 ALLOW 규칙과도 일치하지 않으므로 해당 네임스페이스의 모든 요청을 거부합니다. 여기에 필요한 호출 경로만 ALLOW로 엽니다.

```yaml
apiVersion: security.istio.io/v1
kind: AuthorizationPolicy
metadata:
  name: allow-nothing
  namespace: default
spec: {}
---
apiVersion: security.istio.io/v1
kind: AuthorizationPolicy
metadata:
  name: allow-frontend
  namespace: default
spec:
  selector:
    matchLabels:
      app: backend
  action: ALLOW
  rules:
  - from:
    - source:
        principals: ["cluster.local/ns/default/sa/frontend"]
    to:
    - operation:
        methods: ["GET"]
```

평가 순서는 YAML 작성 순서가 아니라 CUSTOM, DENY, ALLOW 순입니다. 워크로드에 ALLOW 정책이 하나도 없으면 CUSTOM이나 DENY가 막지 않는 요청은 모두 허용되고, ALLOW 정책이 하나라도 적용되면 그중 하나에 일치해야 통과합니다. 명시적 DENY에 `rules: [{}]`를 넣으면 어떤 ALLOW로도 풀 수 없는 전면 차단이 되므로, 기본 거부는 DENY가 아니라 빈 ALLOW 형태로 만드는 것이 맞습니다. `principals`나 `namespaces` 조건은 인증된 mTLS 피어 신원이 있어야 의미가 있으니, STRICT 전환과 짝을 이뤄 적용합니다. 적용 후에는 `istioctl x authz check <pod> -n <namespace>`로 유효 정책을 확인합니다.

### RequestAuthentication은 토큰을 "요구"하지 않습니다

최종 사용자 JWT 검증에는 RequestAuthentication을 씁니다. 여기서 흔히 놓치는 점이 있습니다. RequestAuthentication은 **잘못된** 토큰은 거부하지만, 토큰이 **없는** 요청은 기본적으로 통과시킵니다. 토큰을 필수로 만들려면 AuthorizationPolicy로 `requestPrincipals`를 요구해야 합니다.

```yaml
apiVersion: security.istio.io/v1
kind: RequestAuthentication
metadata:
  name: jwt-auth
  namespace: default
spec:
  selector:
    matchLabels:
      app: myapp
  jwtRules:
  - issuer: "https://auth.example.com"
    jwksUri: "https://auth.example.com/.well-known/jwks.json"
    audiences: ["my-api"]
---
apiVersion: security.istio.io/v1
kind: AuthorizationPolicy
metadata:
  name: require-jwt
  namespace: default
spec:
  selector:
    matchLabels:
      app: myapp
  action: ALLOW
  rules:
  - from:
    - source:
        requestPrincipals: ["*"]
```

RequestAuthentication은 토큰을 검증할 뿐 로그인, OAuth 리다이렉트, 토큰 갱신은 하지 않습니다. issuer와 JWKS 주소는 발급자의 discovery 문서에서 정확히 확인합니다. Amazon Cognito를 쓴다면 ID token과 access token의 `aud` 처리 방식이 달라서, API 접근에는 `token_use=access`와 의도한 `client_id`를 AuthorizationPolicy 조건으로 확인하라고 문서 세트는 안내합니다. ambient에서는 JWT 같은 L7 정책이 waypoint에서 집행되므로 sidecar의 `selector` 대신 waypoint를 대상으로 `targetRefs`를 연결해야 합니다.

<h2 id="resilience">6. 복원력: outlier detection, circuit breaking, locality</h2>

### Outlier detection

Outlier detection은 각 프록시가 관측한 응답을 바탕으로 문제가 있는 엔드포인트를 일정 시간 부하 분산 후보에서 빼는 passive 검사입니다. 파드를 삭제하거나 고치는 기능은 아니고, 메시 전체가 함께 판정하는 것도 아닙니다. 각 호출자 프록시가 각자 판단합니다.

```yaml
apiVersion: networking.istio.io/v1
kind: DestinationRule
metadata:
  name: reviews-outlier
  namespace: default
spec:
  host: reviews
  trafficPolicy:
    outlierDetection:
      consecutive5xxErrors: 5
      interval: 30s
      baseEjectionTime: 30s
      maxEjectionPercent: 50
```

istio.io 참조 문서 기준 기본값은 `consecutive5xxErrors` 5, `interval` 10s, `baseEjectionTime` 30s, `maxEjectionPercent` 10%, `minHealthPercent` 0%입니다. 몇 가지 동작을 알아 두면 설정 의도를 잘못 이해하는 일을 줄일 수 있습니다.

- 연속 실패가 임계치에 닿으면 `interval`을 기다리지 않고 바로 제외될 수 있습니다. `interval`은 주기적 sweep 간격입니다.
- 같은 호스트가 반복해서 제외되면 제외 시간은 `baseEjectionTime`에 제외 횟수를 곱한 만큼 길어집니다.
- `maxEjectionPercent`를 100으로 올리면 모든 엔드포인트가 빠질 수 있고, 남은 용량이 없으면 "no healthy upstream"이 됩니다.
- `minHealthPercent`를 0보다 크게 주면 정상 호스트 비율이 그 아래로 떨어졌을 때 outlier detection을 끄고 비정상 호스트까지 섞어 쓰는 panic 동작이 됩니다.
- 지연 시간 기반 제외는 DestinationRule outlier detection의 기능이 아닙니다. 느린 응답은 timeout으로 다룹니다.

### Circuit breaking (connection pool)

Connection pool 설정은 프록시가 한 업스트림 cluster에 대해 열 수 있는 연결과 대기 요청 수를 제한합니다. 한도를 넘는 HTTP 요청은 대기하지 않고 즉시 실패(fail fast)하며, 이때 응답 플래그 `UO`(upstream overflow)와 함께 503이 보입니다. 이 한도도 서버 파드의 전역 처리 한도가 아니라 각 호출자 프록시 단위라는 점을 기억해야 합니다. 호출자 replica가 늘면 백엔드가 받는 총량도 늘어납니다. 예제 값(`maxConnections: 100`, `http1MaxPendingRequests` 등)은 시작점일 뿐이며 부하 시험으로 정해야 합니다.

### Locality(zone-aware) 라우팅과 cross-AZ

EKS 노드에는 `topology.kubernetes.io/region`과 `topology.kubernetes.io/zone` 레이블이 자동으로 붙고, istiod는 파드가 올라간 노드의 레이블로 엔드포인트의 locality를 파악합니다. 그래서 파드에 AZ 레이블을 따로 붙일 필요가 없습니다.

여기서 꼭 알아야 할 동작이 하나 있습니다. istio.io에 따르면 Istio 기본 메시 구성은 locality 로드 밸런싱이 켜져 있어서, DestinationRule에 `outlierDetection`을 설정하는 순간 locality 기반 failover가 함께 활성화됩니다. 같은 AZ 엔드포인트를 우선 쓰다가, outlier detection이 비정상으로 판정하면 다음 locality로 넘어가는 방식입니다. 반대로 말하면 outlier detection이 없으면 failover 판단 근거도 없습니다. 이 암묵적 동작을 원하지 않으면 `localityLbSetting.enabled: false`를 명시합니다.

```yaml
apiVersion: networking.istio.io/v1
kind: DestinationRule
metadata:
  name: myapp
  namespace: default
spec:
  host: myapp
  trafficPolicy:
    loadBalancer:
      localityLbSetting:
        enabled: true
    outlierDetection:
      consecutive5xxErrors: 5
      interval: 10s
      baseEjectionTime: 30s
```

`distribute`로 "같은 AZ 80, 다른 AZ 20"처럼 비율을 고정할 수도 있지만, 이 경우 20%는 대기용 failover가 아니라 평상시에도 흐르는 cross-AZ 트래픽입니다. `distribute`와 `failover`(또는 `failoverPriority`)는 함께 설정하지 않습니다.

비용 측면에서 보면, 같은 AZ 안에서 호출이 끝날수록 AZ 간 데이터 전송이 줄어드는 방향인 것은 맞습니다. 다만 실제 절감은 과금되는 바이트, 방향, 리전, 경로에 걸린 AWS 서비스에 따라 달라지고, 요청 수나 가중치만으로 계산할 수 없습니다. 같은 AZ로 트래픽을 몰면 그 AZ의 엔드포인트가 과부하될 수 있으니, AZ별 용량을 고르게 유지하는 것이 전제입니다. [EKS 네트워크 비용 최적화 가이드](https://docs.aws.amazon.com/eks/latest/best-practices/cost-opt-networking.html)와 실제 청구 데이터를 함께 보세요.

<h2 id="aws">7. AWS 통합 지점</h2>

### Ingress gateway 앞단의 로드 밸런서

Istio ingress gateway 앞에는 AWS Load Balancer Controller로 NLB나 ALB를 둡니다. 문서 세트가 정리한 선택 기준은 이렇습니다.

| 구분 | NLB 앞단 | ALB 앞단 |
|---|---|---|
| 계층 | L4 | L7 |
| TLS 종료 | TCP 패스스루로 Istio에서 종료하거나 NLB TLS 리스너에서 종료 | ALB에서 ACM 인증서로 종료 |
| 잘 맞는 경우 | gRPC, WebSocket 같은 장시간 연결, 고정 IP, Istio에서 TLS 종료 | 경로 기반 라우팅, AWS WAF, Cognito나 OIDC 인증 연동 |
| 게이트웨이 Service | `type: LoadBalancer`에 NLB annotation | ClusterIP로 두고 Ingress로 ALB 생성 |

공통으로 챙길 것은 다음과 같습니다.

- 대상 유형은 `ip`로 두고, 헬스 체크는 게이트웨이의 상태 포트 `15021`의 `/healthz/ready`를 봅니다.
- ALB 구성에서는 게이트웨이 Service를 ClusterIP로 만들어 불필요한 NLB가 따로 생기지 않게 합니다. ALB가 TLS를 종료하고 기본적으로 HTTP/1.1로 넘기므로, 뒤쪽 Istio Gateway는 HTTP 리스너로 받고 HTTPS 리다이렉트를 넣지 않습니다.
- NLB에서 ACM으로 TLS를 종료하면 게이트웨이에는 평문 HTTP가 도착합니다. 이 경우 Istio Gateway는 해당 포트를 HTTP 리스너로 받아야 합니다.
- ACM 인증서 ARN을 Istio의 `credentialName`에 직접 넣을 수는 없습니다. Istio에서 TLS를 종료하려면 인증서를 Kubernetes Secret으로 넣고 갱신도 자동화해야 합니다(예: ACM Private CA로 발급한 인증서).
- 로드 밸런서 scheme(internal 또는 internet-facing)은 조직의 외부 노출 정책에 맞춰 정하고, 보안 그룹은 최소 권한으로 둡니다.

TLS 종료 위치는 트레이드오프입니다. 로드 밸런서에서 끝내면 ACM 자동 갱신 덕에 관리가 쉽고, Istio에서 끝내면 종단 간 암호화와 세밀한 TLS 정책을 가져갈 수 있습니다.

### AWS API 자격 증명: IRSA와 Pod Identity

메시 안의 워크로드가 S3 같은 AWS API를 호출할 때는 IRSA나 EKS Pod Identity를 그대로 씁니다. 여기서 헷갈리기 쉬운 점은 두 신원 체계가 서로 다른 일을 한다는 것입니다. IRSA와 Pod Identity는 **AWS API 접근** 자격 증명이고, Istio의 SPIFFE 신원은 **메시 안 서비스 간** 인증에 쓰입니다. 어느 쪽도 다른 쪽을 대체하지 않습니다. 둘 다 ServiceAccount를 기준으로 하므로, ServiceAccount 설계를 한 번 잘 해 두면 양쪽 정책이 함께 깔끔해집니다.

### 관찰성 백엔드

- **CloudWatch Container Insights**: CloudWatch agent의 Prometheus 수집으로 istiod와 Envoy 메트릭을 가져올 수 있습니다. 스크래핑 설정만으로는 CloudWatch 메트릭이 게시되지 않고, EMF 프로세서의 metric declaration에 원하는 메트릭(예: `istio_requests_total`)을 선언해야 합니다. 액세스 로그는 Prometheus 경로로 수집되지 않으니 로그 수집기를 따로 둡니다.
- **분산 추적**: 프록시가 span을 만들어 주지만, 요청을 하나의 trace로 잇는 것은 애플리케이션이 추적 헤더를 전파해야 가능합니다. AWS X-Ray나 Jaeger를 백엔드로 쓸 수 있습니다.
- **Prometheus, Grafana, Kiali**: 상세 메트릭과 서비스 토폴로지 시각화에 씁니다.

### EKS 운영 설정

- istiod는 2개 이상의 replica로 운영합니다.
- 게이트웨이에는 HPA와 PodDisruptionBudget을 둡니다. 차트가 이미 HPA를 관리한다면 중복으로 만들지 말고 차트 값을 바꿉니다.
- sidecar 리소스는 requests와 limits를 명시합니다. 문서 세트의 일반 워크로드 시작값(CPU 100m, 메모리 128Mi)은 출발점일 뿐이고 측정으로 조정합니다.

<h2 id="ops">8. 운영에서 자주 막히는 곳</h2>

### 503이 보일 때 먼저 의심할 것

503은 원인이 여러 갈래라서, 증상만 보고 고치기 전에 응답 플래그와 발생 지점을 먼저 봐야 합니다. 문서 세트에 흩어진 원인을 모으면 대략 이렇습니다.

| 상황 | 의심할 원인 | 확인 방법 |
|---|---|---|
| subset으로 보내는 route에서만 실패 | DestinationRule에 subset이 없거나, subset 레이블과 일치하는 ready 엔드포인트가 없음 | `istioctl proxy-config endpoints`, Service selector와 Pod 레이블 |
| STRICT 적용 직후 연결 실패 | 호출자에 sidecar가 없거나, DestinationRule의 TLS override가 DISABLE | `istioctl proxy-config clusters --fqdn ... -o json`, `kubectl get destinationrule -A` |
| 부하가 오를 때만 실패 | connection pool 한도 초과(UO) | Envoy circuit breaker overflow 메트릭 |
| 일부 엔드포인트가 사라짐 | outlier detection 제외, 특히 높은 `maxEjectionPercent` | outlier detection 메트릭, 엔드포인트 상태 |
| 배포나 스케일인 때만 실패 | 파드 종료 순서, 엔드포인트 갱신 지연 | 앱과 프록시 로그, Pod 삭제 시점과 EndpointSlice 변경 |

참고로 404도 Envoy가 낼 수도 있고 애플리케이션이 낼 수도 있습니다. route를 고치기 전에 어느 쪽 응답인지부터 확인합니다.

### Sidecar 주입이 안 될 때

- 네임스페이스 레이블(`istio-injection=enabled` 또는 `istio.io/rev=<revision>`)을 확인합니다. 두 방식을 섞으면 충돌합니다.
- 레이블은 **새로 만들어지는** 파드에만 적용됩니다. 기존 파드에는 sidecar가 붙지 않으니 의도한 워크로드만 롤아웃으로 재생성합니다.
- 파드별 override는 Pod template의 `sidecar.istio.io/inject` **레이블**을 씁니다. 같은 이름의 annotation은 deprecated입니다.
- host-network 파드와 일부 시스템 네임스페이스는 자동 주입에서 제외됩니다.
- native sidecar를 쓰면 프록시가 initContainers에 들어가므로, "프록시가 없다"고 판단하기 전에 containers와 initContainers를 모두 봅니다. `istioctl x check-inject`도 도움이 됩니다.
- ambient로 등록한 워크로드에는 원래 sidecar가 없습니다.

### 시작과 종료 순서

**시작**: 앱이 프록시보다 먼저 떠서 바깥 호출에 실패하는 경우가 있습니다. `holdApplicationUntilProxyStarts: true`는 프록시가 준비될 때까지 앱 컨테이너 시작을 미룹니다. 다만 이것은 시작 순서만 다루며, Envoy가 준비되지 못하는 근본 원인(istiod 연결, 인증서, 구성 거부)을 고치지는 않습니다. "Waiting for Envoy proxy to be ready"가 계속된다면 probe 지연을 늘리기 전에 `istioctl proxy-status`와 istiod 로그를 먼저 봅니다.

**종료**: 일반 containers에 있는 앱과 기존 방식 sidecar는 종료 순서가 보장되지 않습니다. 프록시가 먼저 내려가면 앱의 마지막 요청이 실패합니다. 대응은 세 갈래입니다.

- `proxy.istio.io/config` annotation의 `terminationDrainDuration`으로 프록시 drain 시간을 주고, `spec.terminationGracePeriodSeconds`를 그보다 넉넉하게 잡습니다(`sidecar.istio.io/terminationGracePeriodSeconds`라는 annotation은 존재하지 않습니다).
- Kubernetes native sidecar(`sidecar.istio.io/nativeSidecar: "true"`)를 쓰면 프록시가 주 컨테이너 종료 뒤에 내려갑니다. Kubernetes 기능은 1.33부터 stable이지만 Istio의 해당 annotation은 Alpha로 문서화되어 있다는 점을 감안합니다.
- 무엇보다 앱이 SIGTERM을 받으면 readiness를 내리고 진행 중인 요청을 마무리하도록 구현해야 합니다.

### 업그레이드는 revision 기반 canary로

control plane은 `istioctl install --set revision=<rev>`로 새 버전을 나란히 띄우고, 테스트 네임스페이스부터 `istio.io/rev` 레이블을 바꿔 롤아웃합니다. 모든 프록시와 게이트웨이가 새 revision으로 옮겨 간 것을 `istioctl proxy-status`로 확인한 뒤에야 이전 revision을 제거합니다. 백업에는 설치 입력 파일(또는 Helm values), Istio 리소스와 함께 Gateway API, EnvoyFilter, WasmPlugin 리소스와 CA Secret도 포함해야 합니다.

### 디버깅 도구 정리

```bash
istioctl analyze -A                          # 정적 구성 검사
istioctl proxy-status                        # 프록시 동기화 상태
istioctl proxy-config all <pod> -n <ns>      # 실제 Envoy 구성
istioctl x describe pod <pod> -n <ns>        # 파드에 적용된 정책 요약
istioctl ztunnel-config workloads            # ambient: ztunnel 관점
istioctl bug-report --include <ns>           # 지원 요청용 수집
```

`istioctl analyze`는 정적 검사일 뿐이므로, 최종 판단은 실제 요청을 나르는 프록시의 route, cluster, endpoint로 합니다. ambient에서는 Envoy 명령이 waypoint에만 해당하고, ztunnel은 `ztunnel-config`로 따로 봅니다.

<h2 id="alternatives">9. Istio와 대안 비교</h2>

문서 세트의 비교 장은 기능 별점이나 고정 오버헤드 비율로 순위를 매기지 말고, 아직 충족하지 못한 요구사항과 운영 조건으로 고르라고 강조합니다. 결정에 필요한 핵심만 추리면 다음과 같습니다.

| 후보 | 이럴 때 검토 | 주의할 점 |
|---|---|---|
| Istio | 세밀한 L7 트래픽 제어, 워크로드 신원 기반 mTLS와 인가, 멀티 클러스터, VM 통합이 필요할 때 | control plane과 프록시 수명 주기, 인증서, 정책을 직접 운영 |
| Amazon VPC Lattice | 여러 VPC나 계정에 걸친 서비스 연결, EKS와 EC2, Lambda가 섞인 환경을 관리형으로 연결하고 싶을 때 | fault injection이나 Istio 수준의 circuit breaker 정책 API는 없음. IAM, DNS, 대상 상태 관리는 여전히 사용자 몫 |
| Linkerd | Kubernetes 중심으로 더 단순한 메시를 원할 때 | stable 아티팩트는 vendor가 제공하는 등 릴리스 채널과 지원 조건을 따로 확인 |
| Cilium | L3/L4 네트워크 정책과 eBPF 데이터패스가 주 목적일 때 | L7은 Envoy 같은 프록시가 필요하고, 상호 인증과 암호화(WireGuard, IPsec)는 별도 설정 |

Istio와 VPC Lattice는 배타적이지 않습니다. 클러스터 안은 Istio로 세밀하게 다루고, 클러스터 간이나 계정 간 연결은 Lattice로 처리하는 하이브리드 구성이 가능합니다. 이때 TLS 종료 지점과 호출자 신원을 구간마다 정의해야 하며, 메시의 mTLS가 Lattice의 IAM(SigV4) 인증을 대신하지는 않습니다. 같은 route나 DNS 레코드를 여러 컨트롤러가 동시에 관리하지 않도록 소유권도 나눠 둡니다.

<h2 id="more">10. 더 읽기</h2>

이 글에서 줄인 내용은 원본 문서 세트에 YAML 예제와 함께 자세히 있습니다.

- [Istio 문서 홈](https://www.atomai.click/kubernetes-docs/ko/service-mesh/istio/): 도입 판단과 전체 목차
- [설치 및 초기 설정](https://www.atomai.click/kubernetes-docs/ko/service-mesh/istio/01-installation)
- [아키텍처](https://www.atomai.click/kubernetes-docs/ko/service-mesh/istio/03-architecture): xDS, iptables, Sidecar 리소스
- [AWS 통합](https://www.atomai.click/kubernetes-docs/ko/service-mesh/istio/04-aws-integration): NLB, ALB, ACM, IRSA, CloudWatch
- [Ambient Mode](https://www.atomai.click/kubernetes-docs/ko/service-mesh/istio/advanced/01-ambient-mode)
- [Traffic Management](https://www.atomai.click/kubernetes-docs/ko/service-mesh/istio/traffic-management/)와 [Retry 및 Timeout](https://www.atomai.click/kubernetes-docs/ko/service-mesh/istio/traffic-management/05-retry-timeout)
- [Security](https://www.atomai.click/kubernetes-docs/ko/service-mesh/istio/security/): [mTLS](https://www.atomai.click/kubernetes-docs/ko/service-mesh/istio/security/01-mtls), [인증](https://www.atomai.click/kubernetes-docs/ko/service-mesh/istio/security/02-authentication), [권한 부여](https://www.atomai.click/kubernetes-docs/ko/service-mesh/istio/security/03-authorization)
- [Resilience](https://www.atomai.click/kubernetes-docs/ko/service-mesh/istio/resilience/): [Outlier Detection](https://www.atomai.click/kubernetes-docs/ko/service-mesh/istio/resilience/01-outlier-detection), [Zone Aware Routing](https://www.atomai.click/kubernetes-docs/ko/service-mesh/istio/resilience/03-zone-aware-routing)
- [문제 해결: 일반적인 에러](https://www.atomai.click/kubernetes-docs/ko/service-mesh/istio/troubleshooting/common-errors)
- [모범 사례](https://www.atomai.click/kubernetes-docs/ko/service-mesh/istio/best-practices)
- [대안 비교](https://www.atomai.click/kubernetes-docs/ko/service-mesh/istio/comparison/)

<h2 id="refs">참고 자료</h2>

- [Istio 지원 릴리스 매트릭스](https://istio.io/latest/docs/releases/supported-releases/)
- [Announcing Istio 1.31](https://istio.io/latest/news/releases/1.31.x/announcing-1.31/), [Istio 1.31.1](https://istio.io/latest/news/releases/1.31.x/announcing-1.31.1/), [Istio 1.31.2](https://istio.io/latest/news/releases/1.31.x/announcing-1.31.2/)
- [ISTIO-SECURITY-2026-007](https://istio.io/latest/news/security/istio-security-2026-007/)
- [Amazon EKS 플랫폼 설정 (Istio)](https://istio.io/latest/docs/setup/platform-setup/amazon-eks/)
- [Istio 설치 가이드](https://istio.io/latest/docs/setup/install/)
- [In-cluster operator 사용 중단 안내](https://istio.io/latest/blog/2024/in-cluster-operator-deprecation-announcement/)
- [Ambient overview](https://istio.io/latest/docs/ambient/overview/), [Waypoint 등록](https://istio.io/latest/docs/ambient/usage/waypoint/), [HBONE](https://istio.io/latest/docs/ambient/architecture/hbone/), [Ambient 플랫폼 사전 요구사항](https://istio.io/latest/docs/ambient/install/platform-prerequisites/)
- [Kubernetes Gateway API (Istio)](https://istio.io/latest/docs/tasks/traffic-management/ingress/gateway-api/)
- [VirtualService 참조](https://istio.io/latest/docs/reference/config/networking/virtual-service/), [DestinationRule 참조](https://istio.io/latest/docs/reference/config/networking/destination-rule/)
- [Locality Load Balancing](https://istio.io/latest/docs/tasks/traffic-management/locality-load-balancing/)
- [Istio Security 개념](https://istio.io/latest/docs/concepts/security/), [AuthorizationPolicy 참조](https://istio.io/latest/docs/reference/config/security/authorization-policy/)
- [주입 문제 해결](https://istio.io/latest/docs/ops/common-problems/injection/), [Proxy 진단 도구](https://istio.io/latest/docs/ops/diagnostic-tools/proxy-cmd/)
- [Istio 성능과 확장성](https://istio.io/latest/docs/ops/deployment/performance-and-scalability/)
- [Amazon EKS Kubernetes 버전 수명 주기](https://docs.aws.amazon.com/eks/latest/userguide/kubernetes-versions.html)
- [EKS Pod Identity](https://docs.aws.amazon.com/eks/latest/userguide/pod-identities.html)
- [EKS Best Practices: 네트워크 비용 최적화](https://docs.aws.amazon.com/eks/latest/best-practices/cost-opt-networking.html)
- [AWS Load Balancer Controller](https://kubernetes-sigs.github.io/aws-load-balancer-controller/)
- [CloudWatch Container Insights Prometheus 설정](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/ContainerInsights-Prometheus-Setup.html)
- [Amazon VPC Lattice란](https://docs.aws.amazon.com/vpc-lattice/latest/ug/what-is-vpc-lattice.html)
