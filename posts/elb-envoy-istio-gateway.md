---
title: "ELB for Envoy가 Istio Ingress Gateway를 대체할 수 있을까?"
date: 2026-11-26
description: "도쿄에서 ELB for Envoy의 외부 gRPC 요청 제한을 실측했습니다. 주문 API 예제로 Istio의 서비스 라우팅, 헤더, 카나리 가중치, 사이드카의 Pod당 100 TPS를 어디에서 설정하고 운영하는지 비교합니다."
tags: envoy, istio, elb, eks
---

**ELB for Envoy**를 쓰면 AWS가 Envoy 프록시의 패치와 확장을 맡고, 사용자는 리스너와 라우팅 규칙을 정의합니다. Istio Ingress Gateway의 역할을 어디까지 맡길 수 있을까요? 도쿄에서 관리형 Envoy에 직접 설정을 적용해 보니 경로 재작성, 가중치 라우팅, 로컬 요청 제한이 모두 별도 xDS 서버 없이 동작했습니다. [AWS 출시 공지](https://aws.amazon.com/ko/about-aws/whats-new/2026/11/elastic-load-balancing-for-envoy-generally-available/)

주문 API를 기준으로 보면 경계가 선명해집니다. “주문 요청의 10%를 새 버전으로 보낸다”는 정책에서 Envoy는 목적지를 선택합니다. 새 버전의 Pod가 교체될 때마다 레이블과 준비 상태를 보고 목적지 목록을 갱신하는 쪽은 컨트롤 플레인입니다. “Pod 하나가 초당 100건을 받는다”는 정책은 요청이 **어느 Pod에 도착했는지** 아는 지점에서 집행해야 합니다.

## `order/api`로 보낸다는 뜻

`order` 네임스페이스에 Deployment `api`가 있고 그 Pod에 `app: api`, `version: v1` 레이블이 있다고 가정합니다. Service `api`는 `app: api`를 선택하고 HTTP 포트 8080을 Pod의 8080으로 전달합니다. 같은 네임스페이스에 `app: api, version: v2`인 카나리 Pod를 추가하면 **하나의 Service가 두 버전을 모두 발견**합니다. `DestinationRule`의 두 subset이 버전을 구분합니다. 기본 클러스터 도메인에서 목적지 FQDN은 `api.order.svc.cluster.local`입니다. Deployment 이름만으로 이 연결이 생기지는 않습니다. [Kubernetes Docs의 DestinationRule과 subset 사례](https://docs.atomai.click/kubernetes-docs/ko/service-mesh/istio/traffic-management/03-destination-rule)

다음은 문서의 헤더 조작과 카나리 사례를 주문 API로 옮긴 **Istio 설정 예시**입니다. `istio-system/public-gateway`가 `orders.example.com`을 받는다고 가정합니다. 점검 경로를 먼저 매칭해 프록시가 503을 직접 반환하고, 일반 주문 요청만 백엔드로 보냅니다. [헤더 추가·수정 사례](https://docs.atomai.click/kubernetes-docs/ko/service-mesh/istio/traffic-management/01-gateway-virtualservice) · [직접 응답 사례](https://docs.atomai.click/kubernetes-docs/ko/service-mesh/istio/traffic-management/02-routing) · [가중치 변경 사례](https://docs.atomai.click/kubernetes-docs/ko/service-mesh/istio/traffic-management/04-traffic-splitting)

```yaml
apiVersion: networking.istio.io/v1
kind: DestinationRule
metadata: {name: api, namespace: order}
spec:
  host: api.order.svc.cluster.local
  subsets:
    - {name: stable, labels: {version: v1}}
    - {name: canary, labels: {version: v2}}
---
apiVersion: networking.istio.io/v1
kind: VirtualService
metadata: {name: orders, namespace: order}
spec:
  hosts: [orders.example.com]
  gateways: [istio-system/public-gateway]
  http:
    - match: [{uri: {exact: /orders/maintenance}}]
      directResponse:
        status: 503
        body: {string: "orders under maintenance"}
    - match: [{uri: {prefix: /orders/}}]
      headers:
        request:
          add: {x-order-hop: ingress}
          set: {x-order-source: public-api}
      route:
        - destination: {host: api.order.svc.cluster.local, subset: stable, port: {number: 8080}}
          weight: 90
        - destination: {host: api.order.svc.cluster.local, subset: canary, port: {number: 8080}}
          weight: 10
```

백엔드로 전달할 때 `add`는 기존 `x-order-hop` 값에 `ingress`를 추가하고, `set`은 클라이언트가 보낸 `x-order-source` 값을 `public-api`로 덮어씁니다. 헤더가 없으면 둘 다 생성합니다. 90:10을 70:30으로 바꿀 때는 `VirtualService`의 두 `weight`만 수정합니다. istiod가 같은 두 subset을 가리키는 새 라우트를 배포하므로 이 변경을 위해 Pod 수를 조정할 필요가 없습니다. 단계별 승격을 자동화할 때는 Argo Rollouts가 `VirtualService`의 가중치를 바꾸고, 갱신된 라우트를 Envoy에 전달하는 역할은 계속 istiod가 맡습니다. [Argo Rollouts와 Istio의 역할 분담](https://docs.atomai.click/kubernetes-docs/ko/service-mesh/istio/advanced/08-argo-rollouts)

ELB for Envoy에 직접 설정할 때는 RouteConfiguration의 `request_headers_to_add`에 `APPEND_IF_EXISTS_OR_ADD`와 `OVERWRITE_IF_EXISTS_OR_ADD`를 각각 사용합니다. 가중치는 `weighted_clusters`, 점검 응답은 `direct_response`로 표현합니다. 이 방식에서는 stable·canary 클러스터의 엔드포인트 목록을 직접 공급합니다. 아래 도쿄 실측에서 가중치는 EC2·ECS를 대상으로 검증했고, 헤더 예시는 위 Istio 매니페스트의 동작을 설명합니다.

## 요청을 보내서 확인한 결과

도쿄 실측 환경의 진입 경로는 `CloudFront → 내부 NLB → 내부 ELB for Envoy`였습니다.

2026년 11월 26일, Envoy 1.40에서 EKS 1.37의 Fargate Pod, EC2 인스턴스, ECS Fargate 태스크로 요청을 보냈습니다. EKS와 ECS는 같은 echo 이미지를 사용하고 환경 변수로 자신의 이름을 구별합니다. EC2의 HTTP 서버도 같은 형식으로 이름과 받은 경로를 돌려줍니다. [요청별 실측 JSON](/assets/data/elb-envoy-tokyo/observations.json) · [검증 스크립트](/assets/data/elb-envoy-tokyo/probe.py) · [컨테이너의 echo 서버](/assets/data/elb-envoy-tokyo/echo.go)

| 요청 경로 | 응답 | 확인한 동작 |
| --- | --- | --- |
| `/eks/probe` | HTTP 200, `{"backend":"eks","path":"/probe"}` | EKS Pod가 받은 경로에서 `/eks`가 사라짐 |
| `/ec2/probe` | HTTP 200, `{"backend":"ec2","path":"/probe"}` | EC2에 같은 rewrite 적용 |
| `/ecs/probe` | HTTP 200, `{"backend":"ecs","path":"/probe"}` | ECS 태스크에 같은 rewrite 적용 |
| `/unmatched` | HTTP 404, 본문 없음 | Envoy의 마지막 직접 응답 규칙 적용 |
| `/limited/{n}` 16건 | HTTP 200 **4건**, HTTP 429 **12건** | 429 응답 12건 모두 `x-lab-limit: local` |
| `/split/{n}` 50건 | 모두 HTTP 200, **EC2 43건·ECS 7건** | 두 업스트림에 실제로 분산 |

**버킷을 2로 설정했는데 왜 4건이 통과했을까요?** `/limited/`에는 Envoy의 HTTP `local_ratelimit` 필터를 걸고 버킷 용량 2, 60초마다 토큰 2개 보충, 적용 비율 100%로 설정했습니다. 동시성 4로 보낸 요청 중 첫 2건을 넘겨 성공한 것이 보입니다. 기본 설정에서는 각 Envoy 프로세스가 자체 버킷을 유지합니다. 따라서 한 프로세스가 토큰을 다 써도 다른 프로세스는 요청을 받을 수 있습니다. 이번 응답에는 프로세스 ID가 없어 성공 건수로 프로세스 수를 확정할 수는 없습니다. [Envoy 로컬 요청 제한](https://www.envoyproxy.io/docs/envoy/latest/configuration/http/http_filters/local_rate_limit_filter)

**80 대 20은 요청마다 적용하는 가중치입니다.** 이번 표본에서 EC2는 86%, ECS는 14%를 받았습니다. 50건을 보낼 때마다 정확하게 40건과 10건이 되는 할당량은 아닙니다. `/split/` 규칙에는 rewrite를 넣지 않아 응답의 경로도 `/split/0`처럼 유지됐습니다.

**xDS 아카이브를 교체해 라우트를 갱신했습니다.** `/revision`은 12:31:56 UTC에 `v1`을 반환했습니다. 세 백엔드의 주소와 새 응답 문자열을 ZIP에 담아 같은 프록시 스택에 적용하자, 12:32:29 UTC에 보낸 요청에는 `v2`가 돌아왔습니다. CloudFormation 변경 세트에서 바뀐 속성은 `Proxy.ConfigurationArchive` 하나였습니다. 두 시각은 각각의 응답을 관찰한 시점입니다. [프록시 설정 업데이트 API](https://docs.aws.amazon.com/elasticloadbalancing/latest/elbproxy/update-proxy-configuration.html)

## 글로벌 요청 제한도 관리형 Envoy에서 동작할까?

Istio에서는 `EnvoyFilter`로 게이트웨이에 `envoy.filters.http.ratelimit`을 추가하고, 라우트의 `rate_limits.actions`가 해당 요청의 descriptor를 만듭니다. 예를 들어 `order/api` 전체의 한도를 두려면 모든 게이트웨이가 같은 domain과 `generic_key=order-api`를 외부 **Rate Limit Service(RLS)**에 보냅니다. 매 요청에서 gRPC `ShouldRateLimit`을 호출하고, RLS가 `OVER_LIMIT`을 반환하면 프록시가 기본적으로 429를 돌려줍니다. `kubernetes-docs`의 사례는 여러 RLS 복제본이 Redis 카운터를 공유하는 구성입니다. [Kubernetes Docs의 글로벌 제한 사례](https://docs.atomai.click/kubernetes-docs/ko/service-mesh/istio/resilience/02-rate-limiting) · [Istio 공식 예제](https://istio.io/latest/docs/tasks/policy-enforcement/rate-limit/)

**직접 구성한 ELB for Envoy 1.40에서도 외부 RLS 호출이 동작했습니다.** Istio CRD를 전달하는 대신 bootstrap의 HTTP 필터 체인에서 router 앞에 같은 필터를 넣었습니다. `/global/` 라우트에는 `generic_key=shared-demo`를 지정했고, 필터의 `envoy_grpc.cluster_name`은 HTTP/2를 사용하는 RLS 업스트림을 가리켰습니다. 실험용 RLS 한 프로세스가 `envoy-blog` domain과 이 descriptor를 검사하고 첫 요청부터 60초 동안 합계 **2건**을 허용했습니다. [Envoy HTTP rate limit 필터](https://www.envoyproxy.io/docs/envoy/latest/configuration/http/http_filters/rate_limit_filter)

| RLS가 관찰한 Envoy 연결 출발지 | RLS `OK` → HTTP 200 | RLS `OVER_LIMIT` → HTTP 429 |
| --- | ---: | ---: |
| 프록시 A | 0 | 9 |
| 프록시 B | 2 | 13 |
| **합계** | **2** | **22** |

24건은 약 0.21초 안에 끝났습니다. RLS가 gRPC 연결에서 읽은 출발지와 판정을 응답 헤더에 넣어 두 프록시가 같은 한도를 썼는지 대조했습니다. 이번에는 단일 RLS의 메모리에 카운터를 뒀습니다. RLS까지 여러 대로 확장하려면 같은 키의 카운터를 공유해야 합니다. 이 실험의 `failure_mode_deny: true` 설정에서 RLS 호출 오류는 기본 500, 정상적인 한도 초과는 429입니다. 장애 시 요청을 통과시킬지 결정하는 것도 RLS 운영 설계에 포함됩니다. [실측에 사용한 필터·라우트 설정과 요청별 결과](/assets/data/elb-envoy-tokyo/global-ratelimit.json)

AWS가 **글로벌 rate limiting 미지원**이라고 명시한 대상은 EKS의 **Envoy Gateway + remote infrastructure provider 통합**입니다. 위 결과는 CloudFormation으로 직접 구성한 관리형 프록시에서 측정했습니다. AWS가 해당 통합의 정책 API까지 지원한다는 근거로 확대할 수는 없습니다. [AWS의 EKS 통합 제약](https://docs.aws.amazon.com/elasticloadbalancing/latest/elbproxy/kubernetes-overview.html#kubernetes-overview-differences)

## VirtualService와 DestinationRule을 Envoy 설정으로 풀면

Istio의 `Gateway`는 게이트웨이가 열 포트·호스트·TLS를 기술합니다. `VirtualService`는 어떤 요청을 어디로 보낼지 정하고, `DestinationRule`은 목적지의 subset과 연결 정책을 정의합니다. istiod는 이 선언과 서비스 레지스트리를 함께 읽어 프록시별 xDS 리소스를 만듭니다. 아래 대응 관계는 기존 정책을 옮길 때 확인할 단위입니다. [Istio Gateway](https://istio.io/latest/docs/reference/config/networking/gateway/) · [VirtualService](https://istio.io/latest/docs/reference/config/networking/virtual-service/) · [DestinationRule](https://istio.io/latest/docs/reference/config/networking/destination-rule/)

| Istio에서 적던 의도 | Envoy에서 준비할 설정 | 이번 실습에서 확인한 범위 |
| --- | --- | --- |
| 게이트웨이의 HTTP 포트와 라우트 바인딩 | Listener와 HTTP connection manager에서 RouteConfiguration 참조 | 8080 리스너와 파일 기반 RDS |
| URI prefix 매칭과 rewrite | Route의 `match.prefix`, `route.prefix_rewrite` | 세 백엔드가 돌려준 URI 확인 |
| 가중치 라우팅 | `weighted_clusters`와 각 목적지의 Cluster | EC2와 ECS 사이 80 대 20 설정 |
| subset에 속한 Pod 선택 | subset별 Cluster를 만들고 레이블과 readiness를 반영해 엔드포인트 갱신 | 실험에서는 준비된 백엔드의 IPv4를 직접 입력 |
| 타임아웃·재시도, 커넥션 풀·이상 호스트 제외 | Route의 `timeout`·`retry_policy`, Cluster의 연결 설정·`circuit_breakers`·`outlier_detection` | 설정 위치를 대조한 항목 |
| TLS origination과 워크로드 신원 | upstream TLS context, 신뢰할 CA와 인증서 공급·갱신 경로. 동적 시크릿은 SDS | 실험의 업스트림은 HTTP |

가령 `/eks/probe` 요청에는 아래 규칙 하나가 적용됩니다. 매칭된 `/eks/`를 `/`로 바꾸고 `eks` 클러스터에 전달합니다. 그 클러스터의 실제 주소는 다른 파일에서 결정합니다. 따라서 라우트의 이름을 그대로 두고 Pod 주소만 갱신할 수 있습니다. [실습의 라우트와 클러스터 생성 코드](/assets/data/elb-envoy-tokyo/render_proxy.py)

```yaml
match:
  prefix: /eks/
route:
  cluster: eks
  prefix_rewrite: /
```

직접 만든 xDS 설정으로 많은 전달 동작을 표현할 수 있어도, Istio 전체의 의미를 일대일로 복사할 수는 없습니다. 네임스페이스별 가시성, 서비스 레이블 해석, 워크로드 인증서 발급·회전, 메시 내부 사이드카로의 정책 배포는 각각 상태를 관찰하고 결정을 내리는 주체가 필요합니다. 관리형 Envoy 자체에도 제약이 있습니다. 예를 들어 동적 모듈과 파일 액세스 로거는 지원하지 않고, 액세스 로그는 OpenTelemetry 또는 ALS 같은 네트워크 싱크로 보냅니다. AWS가 허용하는 스키마와 실제 Envoy 빌드에 포함된 확장도 구별해야 합니다. [관리형 Envoy 기능 제한](https://docs.aws.amazon.com/elasticloadbalancing/latest/elbproxy/envoy-limits.html)

## 별도 xDS 서버는 언제 필요한가

| 설정을 바꾸는 주체 | 팀이 운영하는 부분 | 맞는 상황 |
| --- | --- | --- |
| 배포 파이프라인이 bootstrap과 ZIP 아카이브 전달 | 설정 생성·검증, 주소 갱신, 배포와 롤백 | 대상과 정책이 드물게 바뀌는 인입 게이트웨이. 이번 실습 방식 |
| 자체 xDS 서버가 gRPC/ADS로 전달 | 서버 가용성과 신뢰 설정, 서비스 발견, 스냅샷 일관성, ACK/NACK 관찰 | Pod 교체나 카나리 배포에 맞춰 정책과 엔드포인트를 지속적으로 갱신 |
| EKS의 Envoy Gateway가 Gateway API를 xDS로 변환 | Envoy Gateway와 remote infrastructure provider 컨트롤러 운영, 정책 변환 | Kubernetes에서 Gateway·HTTPRoute를 관리하고 프록시 실행을 AWS에 위임 |

세 방식 모두 AWS는 Envoy 프로세스와 호스트의 패치, AZ별 용량과 확장을 맡습니다. 첫 번째 방식은 별도 상시 xDS 서버 없이 동작합니다. bootstrap에 모든 라우트를 넣을 수도 있고, 이번처럼 리스너를 고정한 채 `/xds/routes.yaml`(RDS)과 `/xds/clusters.yaml`(CDS)을 파일로 읽게 할 수도 있습니다. ZIP만 갱신하면 AWS가 파일을 교체하고, 감시 디렉터리를 설정한 Envoy가 새 내용을 읽습니다. bootstrap 자체를 바꾸면 hot restart가 일어나며 기존 연결의 drain 시간은 60초입니다. [Envoy 실행·설정 갱신 방식](https://docs.aws.amazon.com/elasticloadbalancing/latest/elbproxy/how-envoy-runs.html)

기존 Istio CRD를 그대로 유지하려면 이를 해석하는 istiod 등의 구성 경로와 관리형 프록시 사이의 연결, 신원 발급, SDS, 버전 호환성을 별도로 검증해야 합니다. AWS가 문서화한 EKS 통합은 Envoy Gateway에 `elb-envoy-remote-infra-provider`를 붙이는 방식입니다. 이 컨트롤러가 관리형 프록시를 만들고 Envoy Gateway가 xDS를 제공합니다. 출시 시점의 통합 API는 `v1alpha1`입니다. [EKS 통합의 동작과 제약](https://docs.aws.amazon.com/elasticloadbalancing/latest/elbproxy/kubernetes-overview.html)

## EKS 사이드카의 설정은 어디서 관리하나

사이드카 모드에서는 운영팀이 Git에 둔 `VirtualService`, `DestinationRule`, `EnvoyFilter` 등을 Kubernetes API에 적용합니다. 클러스터의 **istiod**가 이 리소스와 Service·EndpointSlice의 변경을 보고, 어느 워크로드에 어떤 리스너·라우트·클러스터·엔드포인트가 필요한지 계산합니다. Pod 안의 Istio 에이전트가 istiod와의 xDS 연결을 중계하고 `istio-proxy`가 설정을 받아 적용합니다. 워크로드 인증서는 에이전트가 로컬 SDS로 Envoy에 제공합니다. 사이드카 자동 주입은 새 Pod를 만들 때 일어나고, 이후의 라우트·필터 변경은 xDS를 통해 전달됩니다. [Istio 아키텍처와 xDS 흐름](https://docs.atomai.click/kubernetes-docs/ko/service-mesh/istio/03-architecture) · [사이드카 주입과 revision](https://docs.atomai.click/kubernetes-docs/ko/service-mesh/istio/advanced/07-sidecar-injection)

따라서 **관리형 인입 프록시와 Istio 사이드카를 함께 쓰면 설정의 소유자가 둘**입니다. 이번 방식에서 인입 설정은 프록시 스택과 xDS 아카이브로 배포합니다. EKS 내부의 사이드카 정책은 클러스터의 Istio 리소스로 관리합니다. 운영팀은 보통 `istio-system`에서 istiod Deployment의 복제본·리소스와 인증서 신뢰 체계를 관리합니다. 업그레이드는 새 control plane revision을 설치하고, 시험 네임스페이스를 새 revision에 연결한 뒤 Pod를 순차 재생성하는 방식으로 진행할 수 있습니다. `istioctl proxy-status`로 각 프록시가 연결된 istiod와 설정 동기화 상태를 확인합니다. 인입 프록시만 AWS로 옮겨도 사이드카를 쓰는 동안에는 istiod가 계속 필요합니다. 목적지 사이드카가 `STRICT` mTLS를 요구하면 외부 인입 프록시도 목적지가 신뢰하는 클라이언트 인증서와 TLS 설정을 갖춰야 합니다. 도쿄 실측의 업스트림은 평문 HTTP였습니다.

## `order/api`의 각 Pod를 100 TPS로 제한하려면

**가능합니다.** 여기서 TPS는 해당 Pod가 받는 HTTP 요청 수로 정의합니다. `order` 네임스페이스의 `app: api` Pod에 사이드카가 주입돼 있고 8080번 포트의 HTTP 요청을 사이드카가 가로채는 구성을 전제합니다. `order`에 아래 `EnvoyFilter`를 두면 들어오는 요청이 애플리케이션에 닿기 전에 선택된 각 Pod의 Envoy가 자체 버킷을 검사합니다. `workloadSelector`는 **Pod 템플릿의 레이블**을 봅니다. 카나리도 `app: api`면 함께 적용되므로 특정 Deployment에만 걸려면 그 Deployment의 Pod에 고유 레이블을 부여해 선택합니다. 아래 YAML은 도쿄 Fargate 실측과 별개인 사이드카 구성 예시입니다. [Kubernetes Docs의 인바운드 로컬 제한 예제](https://docs.atomai.click/kubernetes-docs/ko/service-mesh/istio/resilience/02-rate-limiting) · [Istio EnvoyFilter 레퍼런스](https://istio.io/latest/docs/reference/config/networking/envoy-filter/)

```yaml
apiVersion: networking.istio.io/v1alpha3
kind: EnvoyFilter
metadata: {name: api-inbound-100rps, namespace: order}
spec:
  workloadSelector:
    labels: {app: api}
  configPatches:
    - applyTo: HTTP_FILTER
      match:
        context: SIDECAR_INBOUND
        listener:
          portNumber: 8080
          filterChain:
            filter:
              name: envoy.filters.network.http_connection_manager
              subFilter: {name: envoy.filters.http.router}
      patch:
        operation: INSERT_BEFORE
        value:
          name: envoy.filters.http.local_ratelimit
          typed_config:
            "@type": type.googleapis.com/envoy.extensions.filters.http.local_ratelimit.v3.LocalRateLimit
            stat_prefix: order_api_inbound
            token_bucket: {max_tokens: 100, tokens_per_fill: 100, fill_interval: 1s}
            filter_enabled: {default_value: {numerator: 100, denominator: HUNDRED}}
            filter_enforced: {default_value: {numerator: 100, denominator: HUNDRED}}
            response_headers_to_add:
              - header: {key: x-order-pod-limit, value: "true"}
                append_action: OVERWRITE_IF_EXISTS_OR_ADD
```

버킷은 토큰 100개로 시작하고 매초 최대 100개까지 다시 채웁니다. 한 Pod가 순간적으로 100건을 처리할 수 있으며 토큰이 바닥난 뒤 보충 전까지는 429를 반환합니다. 어떤 연속된 1초 구간에서도 반드시 100건 이하를 보장하는 슬라이딩 윈도와는 동작이 다릅니다. 같은 HTTP 필터 설정을 쓰는 Pod 3개에 부하가 고르게 분산되면 명목상 합계는 약 300요청/초입니다. 한 Pod로 트래픽이 몰리면 다른 Pod에 토큰이 남아 있어도 그 Pod에서는 429가 납니다. 같은 Pod의 8080으로 들어와 이 필터를 지나는 요청은 메시 내부 호출까지 같은 버킷을 씁니다. 인입 ELB에만 로컬 필터를 두면 목적지 Pod를 선택하기 전에 **게이트웨이 프로세스**의 토큰을 소비하므로 Pod별 예산을 보장할 수 없습니다. 서비스 전체에 고정된 100요청/초가 필요하면 요청들이 같은 descriptor를 쓰도록 글로벌 RLS에 한도를 둡니다.

예제의 세 Istio 리소스를 `istioctl 1.30.2 validate`로 검사했습니다. EnvoyFilter가 버전에 따라 바뀔 수 있는 프록시 내부 구조를 사용한다는 경고가 있으므로, 업그레이드할 때는 실제 인바운드 체인에 필터가 들어갔는지 다시 확인합니다. 적용 후에는 `kubectl -n order get pods -l app=api -L version`으로 대상 Pod를 확인하고 `istioctl proxy-config listeners POD이름 -n order -o json`에서 `order_api_inbound`를 찾습니다. 같은 메시의 클라이언트에서 각 Pod의 8080 인바운드로 부하를 보내면 초과 요청의 429와 `x-order-pod-limit: true`로 실제 집행 위치를 확인할 수 있습니다.

## Pod와 태스크의 주소가 바뀌면

이번에는 Ready 상태의 Pod IP, EC2의 사설 IP, RUNNING 상태의 ECS 태스크 IP를 각각 `STATIC` 클러스터에 넣었습니다. 아래 명령은 세 주소를 받아 실측에 사용한 프록시 스택 템플릿을 만듭니다. 새 Pod나 태스크가 다른 IP를 받으면 새 주소로 템플릿을 다시 생성해 스택에 적용해야 합니다. [설정 생성 코드](/assets/data/elb-envoy-tokyo/render_proxy.py)

```bash
python3 render_proxy.py \
  --eks "$POD_IP" --ec2 "$INSTANCE_IP" --ecs "$TASK_IP" \
  --revision v2 > proxy.json
```

배포 때마다 엔드포인트가 바뀐다면 xDS의 Endpoint Discovery Service(EDS)로 이 갱신을 자동화할 수 있습니다. 이 경우 컨트롤 플레인이 Kubernetes의 EndpointSlice나 ECS의 서비스 발견 정보를 읽고, 요청을 받을 준비가 된 엔드포인트 목록을 Envoy에 전달합니다.

기존 게이트웨이에서 **어떤 정책을 유지할지**가 전환 기준입니다. 외부 요청의 경로 재작성·가중치 라우팅·로컬 버스트 제한이 주된 용도라면, 이번 실험처럼 Envoy 데이터 플레인을 클러스터 밖으로 옮길 수 있습니다. 릴리스마다 Pod 레이블로 카나리를 고르거나 메시 mTLS 신원과 네임스페이스 정책까지 사용한다면, 그 의미를 관리할 컨트롤 플레인과의 통합이 마이그레이션의 중심입니다. 여기서 검증한 대상은 외부 인입 경로이며, istiod 연결과 서비스 간 메시 트래픽은 실험 범위 밖입니다.
