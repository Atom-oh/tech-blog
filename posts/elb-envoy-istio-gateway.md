---
title: "ELB for Envoy가 Istio Ingress Gateway를 대체할 수 있을까?"
date: 2026-11-26
description: "온프레미스 Pod 50개와 EKS 10~300개의 분배를 예로 들어, Gateway API로 Istio 인입 정책을 옮기고 Blue/Green 클러스터 사이에 요청을 단계적으로 이전하는 방법을 도쿄 실측과 함께 살펴봅니다."
tags: envoy, istio, elb, eks, gateway-api
---

**ELB for Envoy**를 쓰면 AWS가 Envoy 프록시의 패치와 확장을 맡고, 사용자는 리스너와 라우팅 규칙을 정의합니다. Istio Ingress Gateway의 역할을 어디까지 맡길 수 있을까요? 도쿄에서 관리형 Envoy에 직접 설정을 적용해 보니 경로 재작성, 가중치 라우팅, 로컬 요청 제한이 모두 별도 xDS 서버 없이 동작했습니다. [AWS 출시 공지](https://aws.amazon.com/ko/about-aws/whats-new/2026/11/elastic-load-balancing-for-envoy-generally-available/)

**Istio YAML을 계속 쓸 수 있는 조건은 그 정책을 해석하는 컨트롤 플레인이 대상 프록시에 연결돼 있다는 것입니다.** ELB for Envoy는 `VirtualService`나 `DestinationRule`을 직접 읽지 않습니다. AWS의 EKS 통합을 선택하면 Envoy Gateway가 `Gateway`·`HTTPRoute`와 정책을 xDS로 변환합니다. 이번 글에서는 주문 API의 리스너, 경로, subset을 이 모델로 옮깁니다. 도쿄에서 실제 요청을 보낸 인입 프록시는 파일 기반 xDS로 구성했고, EKS Pod의 사이드카는 istiod에 연결했습니다. [AWS의 Kubernetes 통합](https://docs.aws.amazon.com/elasticloadbalancing/latest/elbproxy/kubernetes.html)

<h2 id="migration">온프레미스 Pod 50개, EKS Pod 10~300개라면</h2>

**EKS에 준비된 용량과 지금 EKS에 맡길 트래픽은 별개의 결정입니다.** 같은 용량의 Ready Pod를 하나의 풀에 넣으면 EKS의 첫 10개가 받는 기대 비중은 10/60, 약 **16.7%**입니다. 그러나 클러스터 이전 첫날 새 환경에 **5%만** 보내고 싶다면 두 사이트를 별도 backend로 두고 가중치를 **95:5**로 설정합니다. Pod 10개를 그대로 두고도 두 결과가 나오는 이유입니다.

<link rel="stylesheet" href="/assets/data/elb-envoy-tokyo/migration-share.css">
<figure id="hybrid-migration-share" class="migration-share" data-fixed-pods="50" aria-labelledby="migration-share-title">
<header><strong id="migration-share-title">Pod 수로 나눌 때와 이전 비율을 직접 정할 때</strong></header>
<div class="chart-panels">
<section aria-label="Ready Pod 비례 분배 계산">
<strong class="chart-heading">Ready Pod당 같은 몫</strong>
<span class="chart-note">온프레미스 50개 고정 · EKS 10~300개</span>
<div class="share-labels"><span>온프레미스<b data-fixed-share>83.3%</b></span><span>EKS<b data-eks-share>16.7%</b></span></div>
<svg class="share-bar" viewBox="0 0 100 12" preserveAspectRatio="none" aria-hidden="true"><rect class="old-site" data-fixed-bar x="0" y="0" width="83.3333" height="12"></rect><rect class="new-site" data-eks-bar x="83.3333" y="0" width="16.6667" height="12"></rect></svg>
<label class="pod-selector" hidden><span>EKS Ready Pod <strong data-eks-count>10</strong>개</span><input type="range" min="10" max="300" step="1" value="10" aria-label="EKS Ready Pod 수"></label>
<span class="chart-note">EKS 50개면 50%, 300개면 85.7%입니다.</span>
</section>
<section aria-label="Blue에서 Green으로 신규 트래픽을 이전하는 가중치 예시">
<strong class="chart-heading">Blue/Green 클러스터 이전</strong>
<div class="cutover-bars"><div><b class="stage">단계</b><span></span><b>Blue</b><b>Green</b></div>
<div><span class="stage">대기</span><svg class="share-bar" viewBox="0 0 100 10" preserveAspectRatio="none" aria-hidden="true"><rect class="old-site" x="0" y="0" width="100" height="10"></rect><rect class="new-site" x="100" y="0" width="0" height="10"></rect></svg><span>100%</span><span>0%</span></div>
<div><span class="stage">카나리</span><svg class="share-bar" viewBox="0 0 100 10" preserveAspectRatio="none" aria-hidden="true"><rect class="old-site" x="0" y="0" width="95" height="10"></rect><rect class="new-site" x="95" y="0" width="5" height="10"></rect></svg><span>95%</span><span>5%</span></div>
<div><span class="stage">확대</span><svg class="share-bar" viewBox="0 0 100 10" preserveAspectRatio="none" aria-hidden="true"><rect class="old-site" x="0" y="0" width="75" height="10"></rect><rect class="new-site" x="75" y="0" width="25" height="10"></rect></svg><span>75%</span><span>25%</span></div>
<div><span class="stage">절반</span><svg class="share-bar" viewBox="0 0 100 10" preserveAspectRatio="none" aria-hidden="true"><rect class="old-site" x="0" y="0" width="50" height="10"></rect><rect class="new-site" x="50" y="0" width="50" height="10"></rect></svg><span>50%</span><span>50%</span></div>
<div><span class="stage">전환</span><svg class="share-bar" viewBox="0 0 100 10" preserveAspectRatio="none" aria-hidden="true"><rect class="old-site" x="0" y="0" width="0" height="10"></rect><rect class="new-site" x="0" y="0" width="100" height="10"></rect></svg><span>0%</span><span>100%</span></div>
</div><span class="chart-note">새 요청에 적용하는 정책 예시 · 각 행의 합계 100%</span>
</section></div>
<figcaption><b>도쿄 축소 실측.</b> 고정 ECS 대상 1개와 EKS Pod 1개는 120건을 60:60으로 나눴습니다. EKS만 3개로 늘리자 고정 대상 30건, EKS 90건이었고 세 Pod가 각각 30건을 받았습니다. 왼쪽의 50개·300개 시나리오는 계산값입니다. <a href="#hybrid">실측 구성과 요청별 결과</a></figcaption>
</figure>
<script defer src="/assets/data/elb-envoy-tokyo/migration-share.js"></script>

<h3 id="blue-green">Blue에서 천천히 빼고 Green으로 천천히 올리는 방법</h3>

1. **두 클러스터를 동시에 연결합니다.** 이전 중에도 계속 살아 있을 인입 Gateway에서 Blue(기존)와 Green(신규)을 서로 다른 backend로 식별합니다. `HTTPRoute.backendRefs`에서 `api-blue`와 `api-green`을 참조하려면 라우트를 관리하는 클러스터에서 두 이름이 실제 대상으로 해석되고, 프록시에서 양쪽으로 연결돼야 합니다. 같은 이름의 Service를 두 클러스터에 만드는 것만으로 원격 엔드포인트가 발견되지는 않습니다. `ReferenceGrant`는 네임스페이스 간 참조를 승인할 때 쓰며 클러스터 간 발견을 제공하지 않습니다.
2. **100:0에서 시작해 Green을 5%로 엽니다.** 0%인 동안에도 별도 시험 호스트나 헤더 매칭 라우트로 Green의 인증, 데이터 읽기·쓰기와 응답을 확인할 수 있습니다.
3. **동일한 요청을 비교하며 75:25, 50:50, 0:100으로 진행합니다.** 각 단계에서 라우트의 `Accepted`·`ResolvedRefs`와 실제 Blue/Green 응답 수를 확인합니다. Green의 오류율, p95 지연, 포화도와 429 비율을 Blue와 비교해 다음 단계로 갈지 결정합니다. 가중치는 개별 요청의 선택 비율이라 짧은 표본에서 정확히 5%가 나올 필요는 없습니다.
4. **0% 뒤에 Blue를 드레인합니다.** 새 요청이 Green으로 가도 이미 열린 WebSocket이나 장기 gRPC 스트림, 처리 중인 주문은 Blue에 남을 수 있습니다. 활성 연결과 미완료 요청을 확인한 뒤 Blue를 줄입니다. 예를 들어 50:50에서 Green의 오류율이 올라가면 승격을 멈추고 75:25, 95:5로 되돌릴 수 있습니다. 이를 위해 Blue의 용량과 데이터 호환성을 유지합니다. EKS가 300개까지 확장된 시점에 온프레미스 50개가 현재 부하를 전부 감당할 수 있는지는 별도 판단입니다.

첫 카나리 승격 때 변경하는 `HTTPRoute` 규칙은 두 backend의 가중치뿐입니다.

```yaml
backendRefs:
  - {name: api-blue, port: 8080, weight: 95}
  - {name: api-green, port: 8080, weight: 5}
```

위의 백분율은 이전 계획의 예시입니다. 다섯 비율은 Envoy Gateway 1.10의 오프라인 xDS 번역 결과로 확인했습니다. 도쿄에서는 EC2·ECS·EKS 세 목적지의 가중치 변경과 DNS 풀에서 EKS를 1→3개로 늘리는 실험을 수행했습니다. [가중치 번역 결과](/assets/data/elb-envoy-tokyo/order-gateway-xds.json) · [세 백엔드의 실측 결과](/assets/data/elb-envoy-tokyo/backend-matrix.json)

## 요청 경로와 설정 경로

**주문 API Pod 하나에는 컨테이너가 두 개 있습니다.** 요청은 그 Pod의 `istio-proxy` 사이드카를 거쳐 같은 Pod의 `echo` 앱으로 갑니다. 둘은 Pod IP와 네트워크 네임스페이스를 공유합니다. `istio-system`의 별도 `istiod` Pod는 설정을 전달하며 주문 요청은 거치지 않습니다. 그림에서 **EKS 제어 플레인**은 AWS가 운영하는 Kubernetes API·etcd이고, **Istio 제어 플레인**은 플랫폼 팀이 EKS 워커에 설치한 istiod입니다.

<figure class="diagram"><a href="/assets/data/elb-envoy-tokyo/topology.html"><img src="/assets/data/elb-envoy-tokyo/topology.svg" alt="AWS가 운영하는 EKS 제어 플레인에 kube-apiserver와 etcd가 있습니다. EKS 데이터 플레인의 워커에는 istio-system의 별도 istiod Pod와 order의 주문 Pod가 있습니다. 한 주문 Pod 안에 istio-proxy와 echo가 함께 있습니다. 인입 ELB는 파일 설정을 읽고 EC2·ECS·EKS로 요청을 전달합니다."></a><figcaption>초록 실선은 HTTP 요청, 회색 선은 RLS 판정 호출, 보라색 점선은 설정 전달입니다. 점선 테두리 안의 두 상자는 한 Pod의 컨테이너입니다. 같은 구조의 복제본 두 개 중 하나를 그렸습니다. 그림을 누르면 확대할 수 있습니다. 확대 도구의 메뉴는 영어로 표시됩니다.</figcaption></figure>

| 궁금한 점 | 도쿄 실습에서의 답 |
| --- | --- |
| 인입 Envoy의 원본 설정은 어디에 있나? | `render_proxy.py`가 만드는 CloudFormation 템플릿입니다. `Proxy.ConfigurationFile`에는 bootstrap, `Proxy.ConfigurationArchive`에는 라우트와 업스트림을 담은 ZIP을 넣습니다. |
| xDS 파일은 어디서 읽나? | AWS가 ZIP을 프록시 파일시스템에 펼칩니다. bootstrap의 HTTP 리스너가 `/xds/routes.yaml`(RDS)을, 클러스터 설정이 `/xds/clusters.yaml`(CDS)을 참조합니다. 이 인입 프록시에는 상시 xDS 서버가 없습니다. |
| EKS 사이드카는 누가 만드나? | Deployment의 ReplicaSet이 새 Pod를 요청할 때 API 서버가 Istio 주입 웹훅을 호출합니다. 웹훅이 Pod 명세에 `istio-proxy`를 추가하고 kubelet이 실행합니다. 이번 Pod에서 `echo`는 앱 컨테이너, `istio-proxy`는 `restartPolicy: Always`인 네이티브 사이드카였습니다. 정책 변경은 별도 `istio-system/istiod` Deployment의 Pod에서 xDS로 받습니다. |
| EC2와 ECS에도 Envoy가 생기나? | 이 실험의 EC2에는 HTTP 서버가, ECS 태스크에는 echo 컨테이너가 요청을 받았습니다. 둘 다 관리형 Envoy의 원격 업스트림으로 등록했으며 백엔드에 Envoy를 설치하지 않았습니다. EC2에 별도 프록시를 설치하거나 ECS 태스크에 사이드카를 명시하면 그때 추가 프로세스가 생깁니다. 실험에 사용한 RLS는 별도 ECS 태스크입니다. |

[관리형 프록시의 설정 생성 코드](/assets/data/elb-envoy-tokyo/render_proxy.py) · [Istio 주입 절차](https://docs.atomai.click/kubernetes-docs/ko/service-mesh/istio/advanced/07-sidecar-injection)

## EC2와 ECS에는 프록시를 누가 넣나

도쿄에서 **EC2에는 HTTP 서버 프로세스를, ECS의 echo 태스크에는 앱 컨테이너 하나를** 띄웠습니다. 관리형 Envoy가 두 백엔드의 IP로 연결했기 때문에 양쪽에 Istio를 설치할 필요가 없었습니다. 아래 그림은 백엔드에도 프록시를 두기로 했을 때 선택할 수 있는 세 가지 배치입니다. 세 줄은 서로 다른 선택지입니다. [실측 EC2 워크로드](/assets/data/elb-envoy-tokyo/workloads.yaml) · [실측 ECS 태스크 정의](/assets/data/elb-envoy-tokyo/ecs.yaml)

<figure class="diagram"><a href="/assets/data/elb-envoy-tokyo/placements.html"><img src="/assets/data/elb-envoy-tokyo/placements.svg" alt="EC2의 Istio VM 구성에서는 플랫폼 팀이 같은 VM에 Istio 에이전트와 Envoy를 설치합니다. ECS에서 자체 구성하면 ECS가 태스크 정의대로 프록시와 앱 컨테이너를 같은 태스크에 시작합니다. Service Connect를 사용하면 ECS가 새 태스크에 AWS 관리 프록시를 추가합니다."></a><figcaption>점선은 설치·시작 주체, 실선은 프록시를 통과하는 요청입니다. 오른쪽 테두리가 인스턴스 또는 태스크 한 개입니다.</figcaption></figure>

| 배치 | 프록시가 실행되는 위치 | 누가 시작하고 설정하나 |
| --- | --- | --- |
| **EC2를 Istio 메시로 편입** | 앱과 같은 VM에 별도 Envoy 프로세스 | 플랫폼 팀이 AMI·cloud-init·SSM 등으로 Istio VM 패키지와 bootstrap·신뢰 자료를 배치합니다. 호스트의 `istio` systemd 서비스가 에이전트를 시작하고 에이전트가 Envoy를 실행합니다. VM을 `WorkloadGroup`/`WorkloadEntry`로 등록하고 연결하면 istiod가 xDS를 공급합니다. |
| **ECS 태스크에 Istio 호환 프록시를 직접 구성** | `awsvpc` 태스크 안에 앱과 별도 프록시 컨테이너 | 플랫폼 팀이 태스크 정의의 `containerDefinitions`에 둘을 명시하고 ECS가 새 태스크를 시작합니다. EKS의 주입 웹훅은 이 경로에 관여하지 않습니다. 외부 워크로드 등록·신원·istiod 연결과 앱으로 가는 포트 경로까지 설계해야 합니다. Fargate에서는 `NET_ADMIN`을 추가할 수 없어 Kubernetes의 iptables 초기화 방식을 그대로 적용할 수 없습니다. |
| **ECS Service Connect 사용** | Service Connect가 활성화된 서비스의 각 새 태스크에 관리형 프록시 컨테이너 | 서비스 설정을 켜면 **ECS가 추가하고 관리**합니다. 해당 프록시는 사용자가 작성한 태스크 정의에 나타나지 않으며 Istio CRD 대신 ECS 서비스 설정을 따릅니다. |

[Istio의 VM 설치 절차](https://istio.io/latest/docs/setup/install/virtual-machine/) · [ECS Service Connect 프록시 배치](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/service-connect-concepts-deploy.html) · [Fargate Linux 기능 제한](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/fargate-tasks-services.html)

<h2 id="gateway-api">주문 API를 Istio에서 Gateway API로 옮기면</h2>

Istio의 `Gateway`는 외부에서 받을 포트와 호스트를, `VirtualService`는 주문 요청의 경로를 정했습니다. 전환 후에는 **플랫폼 팀의 `Gateway`가 리스너를 열고 앱 팀의 `HTTPRoute`가 그 리스너에 붙습니다.** `DestinationRule`의 `stable`·`canary` subset은 `order` 네임스페이스의 Service 두 개로 풀어냅니다. `api-stable`은 `app: api, version: v1`을, `api-canary`는 `app: api, version: v2`를 선택합니다. 두 Service의 포트와 targetPort는 모두 8080입니다. [기존 Istio subset 사례](https://docs.atomai.click/kubernetes-docs/ko/service-mesh/istio/traffic-management/03-destination-rule) · [Gateway API HTTPRoute](https://gateway-api.sigs.k8s.io/reference/api-types/httproute/)

AWS remote provider v1.0.0 호환표는 Envoy Gateway 1.10, Gateway API 1.6.1, Envoy 1.40을 명시합니다. 아래 예제는 AWS 가이드대로 Remote 모드로 구성한 Envoy Gateway와 `elb-proxy-aws` GatewayClass, `order` 네임스페이스를 전제로 합니다. **내부 ELB를 만들려면** 같은 네임스페이스의 `ELBProxyConfiguration`이 `targetRefs`로 이 Gateway를 가리키고 프록시 IAM 역할과 `ipv4Addressability: internal`을 지정해야 합니다. AWS 측 설정과 Service까지 포함한 파일에서 예시 역할 ARN을 교체할 수 있습니다. [주문 API 전체 YAML](/assets/data/elb-envoy-tokyo/order-gateway-api.yaml) · [AWS 설치·연결 단계](https://docs.aws.amazon.com/elasticloadbalancing/latest/elbproxy/kubernetes-get-started.html) · [릴리스 호환표](https://github.com/aws/elb-envoy-remote-infra-provider/blob/v1.0.0/docs/compatibility.md)

```yaml
apiVersion: gateway.networking.k8s.io/v1
kind: Gateway
metadata: {name: orders-ingress, namespace: order}
spec:
  gatewayClassName: elb-proxy-aws
  listeners:
    - name: http
      protocol: HTTP
      port: 80
      hostname: orders.example.com
      allowedRoutes: {namespaces: {from: Same}}
---
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata: {name: orders, namespace: order}
spec:
  parentRefs: [{name: orders-ingress, sectionName: http}]
  hostnames: [orders.example.com]
  rules:
    - matches: [{path: {type: PathPrefix, value: /orders}}]
      filters:
        - type: RequestHeaderModifier
          requestHeaderModifier:
            add: [{name: x-order-hop, value: ingress}]
            set: [{name: x-order-source, value: public-api}]
        - type: URLRewrite
          urlRewrite: {path: {type: ReplacePrefixMatch, replacePrefixMatch: /}}
      timeouts: {request: 3s}
      backendRefs:
        - {name: api-stable, port: 8080, weight: 90}
        - {name: api-canary, port: 8080, weight: 10}
```

예를 들어 `orders.example.com/orders/123`에 들어온 요청은 선택된 앱에 `/123`으로 도착합니다. `add`는 기존 `x-order-hop`에 값을 더하고 `set`은 `x-order-source`를 덮어씁니다. 비중을 90:10에서 70:30으로 옮길 때는 두 `backendRefs.weight`를 수정합니다. 예제는 `HTTPRoute`와 Gateway가 같은 네임스페이스에 있어 `allowedRoutes.namespaces.from: Same`을 사용합니다.

점검 URI `/orders/maintenance`는 별도 `HTTPRoute`의 `Exact` 매치가 일반 `PathPrefix`보다 구체적입니다. 이 규칙은 `ExtensionRef`로 Envoy Gateway의 `HTTPRouteFilter`를 참조해 백엔드 호출 없이 503을 반환합니다. `BackendTrafficPolicy`는 일반 주문 라우트 `orders`만 대상으로 인입 로컬 한도 100요청/초를 설정합니다. 따라서 점검 응답, 게이트웨이 한도, 뒤에서 실측한 **목적지 Pod당 100 TPS**가 각각 어느 경로에 적용되는지 구분할 수 있습니다. [직접 응답 확장](https://gateway.envoyproxy.io/docs/tasks/traffic/direct-response/) · [로컬 요청 제한](https://gateway.envoyproxy.io/docs/tasks/traffic/local-rate-limit/)

**검증 범위.** 위 Gateway·라우트·정책을 릴리스 CRD 스키마로 검사하고, 예시 Service 주소를 넣어 `egctl v1.10.0 x translate`로 xDS를 생성했습니다. 결과에는 두 backend의 90:10 가중치, 요청 헤더와 rewrite, 3초 타임아웃, 503 본문과 100토큰/초 버킷이 들어 있습니다. 이 버전의 번역 결과에서 일반 라우트는 Envoy 클러스터 하나를 가리켰고, 90과 10은 EDS 엔드포인트 그룹의 가중치로 나타났습니다. 운영자가 편집한 값은 두 `backendRefs.weight`뿐입니다. 이 검증은 로컬 번역입니다. 아래 도쿄 수치는 직접 구성한 관리형 Envoy에 실제 요청을 보낸 결과입니다. [검증한 xDS 발췌](/assets/data/elb-envoy-tokyo/order-gateway-xds.json)

## EC2·ECS·EKS에서 같은 요청을 비교했습니다

도쿄 실측 환경의 진입 경로는 `CloudFront → 내부 NLB → 내부 ELB for Envoy`였습니다. 2026년 11월 26일 Envoy 1.40에서 EC2의 HTTP 서버, ECS Fargate 태스크, EKS 1.37의 `order/api`로 요청을 보냈습니다. EKS에는 임시 EC2 워커와 Istio 1.30.2를 사용했습니다. 두 Pod 모두 앱과 사이드카가 Ready였고 두 사이드카가 istiod의 LDS·RDS·CDS·EDS를 구독하는 것을 확인했습니다.

| 관리형 Envoy에서 적용한 설정 | EC2 | ECS | EKS |
| --- | --- | --- | --- |
| prefix rewrite 후 앱이 받은 경로 | `/capture/{id}` | `/capture/{id}` | `/capture/{id}` |
| 요청 헤더 `add` / `set` 후 앱이 받은 값 | `seed,ingress` / `public-api` | `seed,ingress` / `public-api` | `seed,ingress` / `public-api` |
| 인입 로컬 제한, 목적지별 16건 | 200 × 4, 429 × 12 | 200 × 4, 429 × 12 | 200 × 4, 429 × 12 |
| 글로벌 RLS, 세 목적지가 한도 3개 공유 | 200 × 1, 429 × 4 | 200 × 1, 429 × 4 | 200 × 1, 429 × 4 |
| 가중치 **60:30:10**, 120건 | **70** | **35** | **15** |
| 가중치 **10:30:60**, 120건 | **10** | **33** | **77** |

헤더 대조군에는 `x-order-hop: seed`와 `x-order-source: original`을 넣었습니다. 수정 경로에서는 첫 번째 헤더에 `ingress`가 추가되고 두 번째 값은 `public-api`로 바뀌었습니다. 표의 값은 **각 앱이 받은 요청을 되돌려 준 응답**입니다. 가중치 두 번을 측정할 때도 응답 본문의 백엔드 이름을 셌고, 두 표본 모두 EKS Pod 두 개에서 응답이 왔습니다. 점검 경로 `/matrix/maintenance`는 Envoy가 직접 `503 orders under maintenance`를 반환했습니다. [세 백엔드의 요청별 원자료](/assets/data/elb-envoy-tokyo/backend-matrix.json) · [검증 스크립트](/assets/data/elb-envoy-tokyo/probe.py)

**429를 어느 계층에서 냈는지도 확인했습니다.** 인입 로컬 제한은 경로별 버킷에 토큰 2개를 설정했고 429에 `x-lab-limit: local`이 붙었습니다. 글로벌 제한에서는 EC2 → ECS → EKS로 한 건씩 먼저 보내 서로 다른 세 앱의 200과 RLS의 `OK`를 확인했습니다. 같은 descriptor를 쓰는 후속 12건에는 모두 `OVER_LIMIT`과 429가 돌아왔습니다. 거부된 요청은 백엔드에 도달하지 않습니다. 뒤의 **Pod당 100 TPS** 실험은 인입 게이트웨이를 거치지 않고 Pod 인바운드에서 따로 측정했습니다.

**xDS 아카이브를 교체해 라우트를 갱신했습니다.** `/revision`은 12:31:56 UTC에 `v1`을 반환했습니다. 세 백엔드의 주소와 새 응답 문자열을 ZIP에 담아 같은 프록시 스택에 적용하자, 12:32:29 UTC에 보낸 요청에는 `v2`가 돌아왔습니다. CloudFormation 변경 세트에서 바뀐 속성은 `Proxy.ConfigurationArchive` 하나였습니다. 두 시각은 각각의 응답을 관찰한 시점입니다. [프록시 설정 업데이트 API](https://docs.aws.amazon.com/elasticloadbalancing/latest/elbproxy/update-proxy-configuration.html)

<details><summary>초기 Fargate 실험 결과 보기</summary>
<figure class="diagram"><a href="/assets/elb-envoy-istio-gateway-scope.svg"><img src="/assets/elb-envoy-istio-gateway-scope.svg" alt="초기 Fargate 실험에서 세 백엔드의 경로 재작성, 두 백엔드의 가중치 분산, 인입 로컬·글로벌 제한과 ZIP 갱신을 검증한 요약."></a><figcaption>첫 회차에는 EKS Fargate Pod에 사이드카가 없었습니다. 두 목적지 가중치와 공유 한도 2건을 쓴 이 기록은 위 세 목적지 재측정 전의 결과입니다.</figcaption></figure>
</details>

## 글로벌 요청 제한도 관리형 Envoy에서 동작할까?

Istio에서는 `EnvoyFilter`로 게이트웨이에 `envoy.filters.http.ratelimit`을 추가하고, 라우트의 `rate_limits.actions`가 해당 요청의 descriptor를 만듭니다. 예를 들어 `order/api` 전체의 한도를 두려면 모든 게이트웨이가 같은 domain과 `generic_key=order-api`를 외부 **Rate Limit Service(RLS)**에 보냅니다. 매 요청에서 gRPC `ShouldRateLimit`을 호출하고, RLS가 `OVER_LIMIT`을 반환하면 프록시가 기본적으로 429를 돌려줍니다. `kubernetes-docs`의 사례는 여러 RLS 복제본이 Redis 카운터를 공유하는 구성입니다. [Kubernetes Docs의 글로벌 제한 사례](https://docs.atomai.click/kubernetes-docs/ko/service-mesh/istio/resilience/02-rate-limiting) · [Istio 공식 예제](https://istio.io/latest/docs/tasks/policy-enforcement/rate-limit/)

**직접 구성한 ELB for Envoy 1.40에서도 외부 RLS 호출이 동작했습니다.** Istio CRD를 전달하는 대신 bootstrap의 HTTP 필터 체인에서 router 앞에 같은 필터를 넣었습니다. 첫 회차의 `/global/` 라우트에는 `generic_key=shared-demo`를 지정했고, 필터의 `envoy_grpc.cluster_name`은 HTTP/2를 사용하는 RLS 업스트림을 가리켰습니다. 실험용 RLS 한 프로세스가 `envoy-blog` domain과 이 descriptor를 검사하고 첫 요청부터 60초 동안 합계 **2건**을 허용했습니다. [Envoy HTTP rate limit 필터](https://www.envoyproxy.io/docs/envoy/latest/configuration/http/http_filters/rate_limit_filter)

| RLS가 관찰한 Envoy 연결 출발지 | RLS `OK` → HTTP 200 | RLS `OVER_LIMIT` → HTTP 429 |
| --- | ---: | ---: |
| 프록시 A | 0 | 9 |
| 프록시 B | 2 | 13 |
| **합계** | **2** | **22** |

24건은 약 0.21초 안에 끝났습니다. RLS가 gRPC 연결에서 읽은 출발지와 판정을 응답 헤더에 넣어 두 프록시가 같은 한도를 썼는지 대조했습니다. 이번에는 단일 RLS의 메모리에 카운터를 뒀습니다. RLS까지 여러 대로 확장하려면 같은 키의 카운터를 공유해야 합니다. 이 실험의 `failure_mode_deny: true` 설정에서 RLS 호출 오류는 기본 500, 정상적인 한도 초과는 429입니다. 장애 시 요청을 통과시킬지 결정하는 것도 RLS 운영 설계에 포함됩니다. [실측에 사용한 필터·라우트 설정과 요청별 결과](/assets/data/elb-envoy-tokyo/global-ratelimit.json)

AWS가 **글로벌 rate limiting 미지원**이라고 명시한 대상은 EKS의 **Envoy Gateway + remote infrastructure provider 통합**입니다. 위 결과는 CloudFormation으로 직접 구성한 관리형 프록시에서 측정했습니다. AWS가 해당 통합의 정책 API까지 지원한다는 근거로 확대할 수는 없습니다. [AWS의 EKS 통합 제약](https://docs.aws.amazon.com/elasticloadbalancing/latest/elbproxy/kubernetes-overview.html#kubernetes-overview-differences)

## Istio 기능별로 무엇을 옮기고 무엇을 별도로 운영하나

| 기존 Istio에서 하던 일 | Gateway API 또는 Envoy Gateway에서 설정할 곳 | 적용 범위와 차이 |
| --- | --- | --- |
| Istio `Gateway`의 호스트·포트·TLS 종료 | 표준 `Gateway.spec.listeners`의 `hostname`·`port`·`tls.certificateRefs` | 게이트웨이 인증서를 Secret으로 참조합니다. HTTP 예제에는 TLS 리스너를 추가해야 합니다. |
| `VirtualService`의 경로·헤더 매칭, add/set/remove, 재작성 | `HTTPRoute.rules.matches`·`RequestHeaderModifier`·`ResponseHeaderModifier`·`URLRewrite` | `PathPrefix`는 경로 요소 단위로 매칭합니다. 정규식, 경계 경로, 필터 조합은 기존 요청 예제로 대조합니다. |
| subset별 가중치·카나리 | 레이블 selector가 다른 Service + `HTTPRoute.backendRefs.weight` | `DestinationRule`의 subset 이름과 Pod 레이블 선택을 두 Service에 옮깁니다. 목적지 가중치와 Pod 수는 독립입니다. |
| `directResponse`로 점검 응답 | `HTTPRoute`의 `ExtensionRef` → Envoy Gateway `HTTPRouteFilter.spec.directResponse` | 표준 Gateway API 1.6.1의 기본 필터 목록에는 direct response가 없습니다. 예제는 확장으로 503을 생성합니다. |
| 요청 타임아웃·재시도·미러링 | `HTTPRoute.rules.timeouts`·`RequestMirror`, Envoy Gateway `BackendTrafficPolicy.spec.retry` | 전체 요청 시간과 backend 요청 시간을 구분합니다. 주문 쓰기 API에 재시도를 옮길 때는 중복 처리 가능성을 확인합니다. |
| 연결 풀·회로 차단·이상 호스트 제외·부하 분산 방식 | `BackendTrafficPolicy`의 `circuitBreaker`·`healthCheck.passive`·`loadBalancer` | Istio의 `DestinationRule.trafficPolicy`를 동작별로 나눠 Gateway 또는 HTTPRoute에 붙입니다. 카운터의 기준과 제외·복귀 조건도 대조합니다. |
| 오류·지연 주입 | Envoy Gateway `BackendTrafficPolicy.spec.faultInjection.abort`·`delay` | 대상 Route를 좁혀 적용할 수 있습니다. 미러링은 별도 `RequestMirror` 필터이며 복제 요청을 받는 앱이 실제 쓰기를 수행하는지도 확인합니다. |
| EnvoyFilter 기반 요청 제한 | 인입 로컬은 `BackendTrafficPolicy.spec.rateLimit.local` | 인입 프록시 인스턴스별 한도입니다. AWS remote provider의 글로벌 RLS 정책은 미지원입니다. 직접 구성한 외부 RLS 실측은 앞 절의 별도 경로입니다. |
| 백엔드 TLS·JWT·접근 정책 | 표준 `BackendTLSPolicy`로 Service에 대한 서버 인증서 검증, Envoy Gateway `SecurityPolicy`로 인입 JWT·인가 | 메시 내부의 `PeerAuthentication`, 워크로드 신원 발급과 동서 트래픽 인가는 별도로 운영합니다. |
| Pod별 인바운드 한도 | 사이드카 모드에서는 기존 Istio `EnvoyFilter`를 `SIDECAR_INBOUND`에 적용 | 인입 `HTTPRoute`로 이전해도 각 Pod에 버킷이 생기지 않습니다. 뒤의 두 Pod에서 이 차이를 측정했습니다. |

`HTTPRoute`는 인입 정책의 선언이고 Envoy Gateway가 이를 xDS로 변환합니다. Istio의 확장을 옮길 때는 어떤 프록시에서 실행했는지부터 확인하는 편이 좋습니다. 같은 100 TPS라도 게이트웨이와 주문 Pod에 적용할 때 보호하는 대상이 달라집니다. [Envoy Gateway 정책 API](https://gateway.envoyproxy.io/docs/api/extension_types/) · [BackendTLSPolicy](https://gateway-api.sigs.k8s.io/reference/api-types/policy/backendtlspolicy/) · [AWS remote provider의 기능별 지원](https://github.com/aws/elb-envoy-remote-infra-provider/blob/v1.0.0/docs/compatibility.md)

<figure class="diagram"><a href="/assets/elb-envoy-crd-to-xds.svg"><img src="/assets/elb-envoy-crd-to-xds.svg" alt="Istio 리소스가 Envoy xDS 설정이 되는 과정. istiod가 Gateway, VirtualService, DestinationRule, EnvoyFilter와 Service, EndpointSlice를 함께 읽어 프록시마다 Listener(LDS), RouteConfiguration(RDS), Cluster(CDS), 엔드포인트(EDS)를 계산하고 gRPC xDS로 푸시한다. ELB for Envoy에는 istiod가 들어 있지 않으며, 도쿄 실습에서는 같은 리소스를 bootstrap의 고정 리스너와 HTTP 필터, /xds/routes.yaml, /xds/clusters.yaml로 직접 만들고 EDS 대신 STATIC 주소나 STRICT_DNS를 썼다. 사이드카는 istiod에서 istio-agent를 거쳐 설정을 받고, 인입 프록시는 CloudFormation이 전달한 ZIP을 파일로 읽는다." loading="lazy"></a><figcaption>Envoy가 받는 것은 CRD가 아니라 LDS·RDS·CDS·EDS 리소스입니다. istiod는 EKS에 따로 설치한 컨트롤 플레인이며 ELB for Envoy에 포함되어 있지 않습니다. 오른쪽 점선 칸이 인입 프록시에서 istiod 대신 쓴 방법입니다.</figcaption></figure>

## 별도 xDS 서버는 언제 필요한가

| 설정을 바꾸는 주체 | 팀이 운영하는 부분 | 맞는 상황 |
| --- | --- | --- |
| 배포 파이프라인이 bootstrap과 ZIP 아카이브 전달 | 설정 생성·검증, 주소 갱신, 배포와 롤백 | 대상과 정책이 드물게 바뀌는 인입 게이트웨이. 이번 실습 방식 |
| 자체 xDS 서버가 gRPC/ADS로 전달 | 서버 가용성과 신뢰 설정, 서비스 발견, 스냅샷 일관성, ACK/NACK 관찰 | Pod 교체나 카나리 배포에 맞춰 정책과 엔드포인트를 지속적으로 갱신 |
| EKS의 Envoy Gateway가 Gateway API를 xDS로 변환 | Envoy Gateway와 remote infrastructure provider 컨트롤러 운영, 정책 변환 | Kubernetes에서 Gateway·HTTPRoute를 관리하고 프록시 실행을 AWS에 위임 |

세 방식 모두 AWS는 Envoy 프로세스와 호스트의 패치, AZ별 용량과 확장을 맡습니다. 첫 번째 방식은 별도 상시 xDS 서버 없이 동작합니다. bootstrap에 모든 라우트를 넣을 수도 있고, 이번처럼 리스너를 고정한 채 `/xds/routes.yaml`(RDS)과 `/xds/clusters.yaml`(CDS)을 파일로 읽게 할 수도 있습니다. ZIP만 갱신하면 AWS가 파일을 교체하고, 감시 디렉터리를 설정한 Envoy가 새 내용을 읽습니다. bootstrap 자체를 바꾸면 hot restart가 일어나며 기존 연결의 drain 시간은 60초입니다. [Envoy 실행·설정 갱신 방식](https://docs.aws.amazon.com/elasticloadbalancing/latest/elbproxy/how-envoy-runs.html)

<figure class="diagram"><a href="/assets/elb-envoy-config-update.svg"><img src="/assets/elb-envoy-config-update.svg" alt="ELB for Envoy의 설정 갱신 두 가지. xDS 파일을 담은 ZIP만 바꾸면 AWS가 새 파일을 제자리로 옮기고 Envoy는 재시작하지 않으며, 감시 디렉터리를 설정한 Envoy가 /xds의 RDS와 CDS 파일을 다시 읽는다. 도쿄에서는 변경 세트의 변경 속성이 Proxy.ConfigurationArchive 하나였고 /revision 응답이 12:31:56 UTC에 v1, 12:32:29 UTC에 v2였다. bootstrap 파일을 바꾸면 envoy --mode validate로 검사한 뒤 hot restart해 새 프로세스가 리스너를 넘겨받고, 이전 프로세스는 60초 동안 기존 연결을 drain한 뒤 종료한다. TCP와 WebSocket 연결은 강제로 닫지 않는다." loading="lazy"></a><figcaption>위 줄은 도쿄 실측, 아래 줄은 AWS 문서의 동작이며 이번에 측정하지 않았습니다. 검증에 실패한 bootstrap은 적용되지 않고 이전 설정이 계속 요청을 처리합니다.</figcaption></figure>

기존 Istio CRD를 그대로 유지하려면 이를 해석하는 istiod 등의 구성 경로와 관리형 프록시 사이의 연결, 신원 발급, SDS, 버전 호환성을 별도로 검증해야 합니다. AWS가 문서화한 EKS 통합은 Envoy Gateway에 `elb-envoy-remote-infra-provider`를 붙이는 방식입니다. 이 컨트롤러가 관리형 프록시를 만들고 Envoy Gateway가 xDS를 제공합니다. 출시 시점의 통합 API는 `v1alpha1`입니다. [EKS 통합의 동작과 제약](https://docs.aws.amazon.com/elasticloadbalancing/latest/elbproxy/kubernetes-overview.html)

## EKS 제어 플레인, istiod, CRD의 운영 주체

`istiod`는 **EKS 기준으로 데이터 플레인에 배치된 워크로드**이고 **Istio 기준으로는 메시 제어 플레인**입니다. 도쿄 실습에서는 `istio-system` 네임스페이스에 `Deployment/istiod`와 `Service/istiod`를 설치했습니다. 이 Deployment의 `discovery` 컨테이너가 EC2 워커에서 실행됐습니다. `order` 네임스페이스의 각 주문 Pod가 그 서비스에 연결됩니다. [EKS 클러스터 구성](https://docs.aws.amazon.com/eks/latest/userguide/clusters.html) · [Istio의 컨트롤 플레인](https://istio.io/latest/docs/ops/deployment/architecture/)

| 구성 요소 | 실제 위치와 범위 | 운영 책임 |
| --- | --- | --- |
| `kube-apiserver` · etcd | AWS 관리 EKS **제어 플레인**. Kubernetes API가 요청을 받고 etcd에 상태를 저장 | AWS가 제어 플레인 인프라와 가용성을 운영합니다. 고객 플랫폼 팀은 클러스터 버전·접근 권한과 어떤 리소스를 적용할지 관리합니다. |
| Istio CRD 정의 | Kubernetes API에 등록하는 **클러스터 범위** 객체. `istio-system`에 속하지 않음 | 플랫폼 팀이 Istio 설치 시 등록하고 버전 업그레이드 시 스키마 호환성을 관리합니다. Helm이라면 `istio/base`가 기본 CRD를 제공합니다. |
| `Deployment/istiod` · `Service/istiod` | `istio-system`. Pod는 EKS 워커에 배치되고 Service가 연결 주소를 제공 | 이 실습처럼 직접 설치한 Istio는 플랫폼 팀이 설치·업그레이드하고 복제본·자원·인증서 신뢰 체계를 관리합니다. K8s가 Pod를 스케줄하고 재시작합니다. |
| `VirtualService` · `DestinationRule` · `EnvoyFilter` 인스턴스 | 예제의 `order` 네임스페이스. 정의된 종류의 **정책 객체** | 권한을 부여받은 앱 팀 또는 플랫폼 팀이 GitOps·kubectl로 적용합니다. istiod가 API를 감시하고 해당 게이트웨이·사이드카에 맞는 설정을 생성합니다. |
| `istio-proxy` · `echo` | EKS **데이터 플레인**의 동일한 `order/api` Pod | 주입 웹훅이 프록시 컨테이너를 Pod 명세에 추가하고 kubelet이 실행합니다. 애플리케이션 팀은 앱을, 플랫폼 팀은 주입 정책과 프록시 버전을 관리합니다. |

**CRD와 정책 객체의 차이**는 조회 명령에서 드러납니다. `envoyfilters.networking.istio.io` CRD는 어떤 필드를 받을지와 인스턴스의 범위를 정합니다. 이 CRD의 `spec.scope: Namespaced`는 **생성할 EnvoyFilter가 네임스페이스에 속한다**는 뜻입니다. 실측 정책의 이름은 `order/api-inbound-100rps`입니다. 같은 원리로 `virtualservices.networking.istio.io`와 `destinationrules.networking.istio.io`는 클러스터에 한 번 등록하고, 각 팀은 권한이 있는 네임스페이스에서 인스턴스를 만듭니다. [Kubernetes의 CRD 범위](https://kubernetes.io/docs/tasks/extend-kubernetes/custom-resources/custom-resource-definitions/) · [Istio Helm 설치](https://istio.io/latest/docs/setup/install/helm/)

```bash
kubectl get crd envoyfilters.networking.istio.io -o jsonpath='{.spec.scope}'
kubectl -n order get envoyfilter api-inbound-100rps
kubectl -n istio-system get deployment/istiod service/istiod
istioctl proxy-status
```

실습에서 클러스터 범위의 `MutatingWebhookConfiguration/istio-sidecar-injector`가 가리킨 대상은 `istio-system`의 `Service/istiod`, 경로는 `/inject`였습니다. 새 Pod가 생성될 때 주입 대상과 맞으면 API 서버가 웹훅을 호출합니다. 이후 istiod가 Service·EndpointSlice·Istio 정책 변경을 읽고, Pod의 Istio 에이전트가 중계하는 xDS 연결을 통해 Envoy에 새 설정을 전달합니다. 워크로드 인증서는 에이전트가 로컬 SDS로 제공합니다. `VirtualService` 수정만으로 기존 Pod를 다시 만들 필요는 없습니다. 인입 ELB도 같은 설정을 받으려면 그 프록시가 해당 컨트롤 플레인의 클라이언트로 연결되어 있어야 합니다.

목적지 사이드카에 `STRICT` mTLS를 요구한다면 외부 인입 프록시에도 목적지가 신뢰하는 클라이언트 인증서와 TLS 설정이 필요합니다. 도쿄 실측의 업스트림은 평문 HTTP였습니다.

## `order/api`의 각 Pod를 100 TPS로 제한하려면

**가능합니다.** 여기서 TPS는 해당 Pod가 받는 HTTP 요청 수로 정의합니다. `order` 네임스페이스의 `app: api` Pod에 사이드카가 주입돼 있고 8080번 포트의 HTTP 요청을 사이드카가 가로채는 구성을 전제합니다. `order`에 아래 `EnvoyFilter`를 두면 들어오는 요청이 애플리케이션에 닿기 전에 선택된 각 Pod의 Envoy가 자체 버킷을 검사합니다. `workloadSelector`는 **Pod 템플릿의 레이블**을 봅니다. 카나리도 `app: api`면 함께 적용되므로 특정 Deployment에만 걸려면 그 Deployment의 Pod에 고유 레이블을 부여해 선택합니다. 아래 YAML을 도쿄 EKS의 `order` 네임스페이스에 적용하고, 두 Pod에서 실제 인바운드 필터 설정을 읽어 버킷 값을 확인했습니다. [Kubernetes Docs의 인바운드 로컬 제한 예제](https://docs.atomai.click/kubernetes-docs/ko/service-mesh/istio/resilience/02-rate-limiting) · [Istio EnvoyFilter 레퍼런스](https://istio.io/latest/docs/reference/config/networking/envoy-filter/)

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

버킷 용량은 100개이고 설정한 보충률은 초당 100토큰입니다. 초기 버스트 동안에도 시간이 흐르면서 토큰을 다시 쓸 수 있습니다. 토큰을 얻지 못한 요청에 사이드카가 429를 반환합니다. 어떤 연속된 1초 구간에서도 반드시 100건 이하를 보장하는 슬라이딩 윈도와는 동작이 다릅니다. 같은 HTTP 필터 설정을 쓰는 Pod 3개에 부하가 고르게 분산되면 명목상 합계는 약 300요청/초입니다. 한 Pod로 트래픽이 몰리면 다른 Pod에 토큰이 남아 있어도 그 Pod에서는 429가 납니다. 같은 인바운드 HTTP 필터 인스턴스를 지나는 요청은 호출자가 달라도 버킷을 공유합니다. 평문과 mTLS가 서로 다른 필터 체인을 쓰는 배포라면 두 경로를 합친 한도도 따로 확인해야 합니다. 인입 ELB에만 로컬 필터를 두면 목적지 Pod를 선택하기 전에 **게이트웨이 프로세스**의 토큰을 소비하므로 Pod별 예산을 보장할 수 없습니다. 서비스 전체에 고정된 100요청/초가 필요하면 요청들이 같은 descriptor를 쓰도록 글로벌 RLS에 한도를 둡니다.

<figure class="diagram"><a href="/assets/elb-envoy-ratelimit-placement.svg"><img src="/assets/elb-envoy-ratelimit-placement.svg" alt="요청 제한 세 가지가 토큰을 쓰는 위치. 인입 로컬 제한은 ELB for Envoy의 라우트별 버킷 2개로 EC2, ECS, EKS 목적지마다 16건 중 4건이 통과하고 12건이 429였으며, 버킷보다 많이 통과한 것은 Envoy 프로세스마다 버킷이 따로 있기 때문이다. 글로벌 제한은 세 목적지가 외부 RLS의 카운터 하나를 공유해 한 건씩 보낸 3건이 OK, 후속 12건이 모두 429였다. 사이드카 제한은 EKS Pod마다 istio-proxy가 버킷 100개를 갖고, 각 Pod IP로 140건을 보내자 Pod A는 107건, Pod B는 102건을 허용했다." loading="lazy"></a><figcaption>같은 “한도”라도 토큰을 쓰는 곳이 다릅니다. 세 칸 모두 도쿄 재측정 값이고, 세 번째 칸은 인입 게이트웨이를 거치지 않고 Pod 인바운드에서 따로 측정했습니다.</figcaption></figure>

실측 당시 `order/api-5cc587ffc9-8h2d2`와 `order/api-5cc587ffc9-zbx47`은 각각 `istio-proxy`와 `echo`가 함께 실행 중인 `2/2 Ready` Pod였습니다. 사이드카가 없는 클라이언트 Job이 각 Pod IP의 8080으로 직접 140건씩 보냈습니다. 200 응답에서는 앱이 돌려준 Pod 이름을, 429에서는 `x-order-pod-limit: true`를 확인했습니다. 두 번째 라운드는 2.1초 이상 쉰 뒤 같은 순서로 실행했습니다.

| EKS 대상 | 첫 버스트 200 / 429 | 보충 후 200 / 429 |
| --- | --- | --- |
| Pod A | 107 / 33 (86 ms) | 101 / 39 (18 ms) |
| Pod B | 102 / 38 (32 ms) | 101 / 39 (18 ms) |

첫 Pod가 제한에 걸린 직후에도 두 번째 Pod는 102건을 허용했습니다. 짧은 버스트에서 성공 건수가 100을 넘은 결과 역시 토큰 버킷의 초기 용량과 측정 중 보충을 함께 봐야 설명할 수 있습니다. 이 수치는 각 Pod의 **평문 HTTP 인바운드**에서 측정했습니다. [버스트별 응답과 실제 Envoy 설정](/assets/data/elb-envoy-tokyo/backend-matrix.json) · [부하를 보낸 Job의 코드](/assets/data/elb-envoy-tokyo/check-pods/main.go)

Pod 인바운드용 Istio `EnvoyFilter`는 `istioctl 1.30.2 validate`로도 검사했습니다. EnvoyFilter가 버전에 따라 바뀔 수 있는 프록시 내부 구조를 사용한다는 경고가 있으므로, 업그레이드할 때는 실제 인바운드 체인에 필터가 들어갔는지 다시 확인합니다. 적용 후에는 `kubectl -n order get pods -l app=api -L version`으로 대상 Pod를 확인하고 `istioctl proxy-config listeners POD이름 -n order -o json`에서 `order_api_inbound`를 찾습니다. 같은 메시의 클라이언트에서 각 Pod의 8080 인바운드로 부하를 보내면 초과 요청의 429와 `x-order-pod-limit: true`로 실제 집행 위치를 확인할 수 있습니다.

## Pod와 태스크의 주소가 바뀌면

첫 경로 재작성 실험에서는 Ready 상태의 Pod IP, EC2의 사설 IP, RUNNING 상태의 ECS 태스크 IP를 각각 `STATIC` 클러스터에 넣었습니다. 아래 명령은 세 주소를 받아 실측에 사용한 프록시 스택 템플릿을 만듭니다. 새 Pod나 태스크가 다른 IP를 받으면 새 주소로 템플릿을 다시 생성해 스택에 적용해야 합니다. [설정 생성 코드](/assets/data/elb-envoy-tokyo/render_proxy.py)

```bash
python3 render_proxy.py \
  --eks "$POD_IP" --ec2 "$INSTANCE_IP" --ecs "$TASK_IP" \
  --revision v2 > proxy.json
```

배포 때마다 엔드포인트가 바뀐다면 xDS의 Endpoint Discovery Service(EDS)로 이 갱신을 자동화할 수 있습니다. 이 경우 컨트롤 플레인이 Kubernetes의 EndpointSlice나 ECS의 서비스 발견 정보를 읽고, 요청을 받을 준비가 된 엔드포인트 목록을 Envoy에 전달합니다.

<h2 id="hybrid">실측으로 확인한 Pod 수 비례 분산</h2>

**마이그레이션의 카나리 단계가 끝나고 Pod당 같은 몫을 주기로 했다면 환경별 `weight`를 갱신할 컨트롤러가 필요 없습니다.** 한 Route가 하나의 Envoy 업스트림 클러스터로 보내고, 그 클러스터에 두 환경의 Ready Pod가 같은 우선순위·가중치의 개별 엔드포인트로 들어가면 됩니다. `ROUND_ROBIN`에서 EKS의 기대 비중은 `EKS Ready Pod 수 / 전체 Ready Pod 수`입니다.

| 온프레미스 Ready Pod | EKS Ready Pod | 온프레미스 기대 비중 | EKS 기대 비중 |
| ---: | ---: | ---: | ---: |
| 50 | 10 | 83.3% | 16.7% |
| 50 | 50 | 50.0% | 50.0% |
| 50 | 300 | 14.3% | 85.7% |

<figure class="diagram"><a href="/assets/elb-envoy-hybrid-distribution.svg"><img src="/assets/elb-envoy-hybrid-distribution.svg" alt="온프레미스 Ready Pod 50개와 EKS Ready Pod 10~300개에 요청을 나누는 두 방식. A는 Envoy 클러스터 하나에 두 사이트의 Ready Pod를 모두 개별 엔드포인트로 넣어, ROUND_ROBIN이면 EKS 비중이 EKS Ready Pod 수를 전체 Ready Pod 수로 나눈 값이 된다. EKS 10개일 때 16.7%, 50개일 때 50%, 300개일 때 85.7%이며 비율을 갱신할 컨트롤러가 필요 없다. B는 weighted_clusters로 사이트 비율을 정하며, EKS 상한을 60%로 두면 300개에서도 60%이고 가변 정책이면 Ready EndpointSlice를 감시해 RDS를 갱신하는 컨트롤러가 필요하다. 도쿄에서는 ECS 태스크 하나를 고정 대상으로 두고 STRICT_DNS 클러스터 하나에 fixed-site와 elastic-site를 넣어 replicas만 바꿔 측정했고, EKS Pod 1개일 때 120건이 60 대 60, 3개일 때 30 대 30·30·30으로 나뉘었다." loading="lazy"></a><figcaption>위쪽 비중은 계산값이고, B 방식과 60% 상한은 예시이며 측정하지 않았습니다. 아래 막대만 도쿄 실측 결과입니다.</figcaption></figure>

**도쿄에서는 DNS 발견 방식으로 이 원리를 축소 실측했습니다.** 실제 온프레미스 대신 ECS 태스크 한 개를 고정 대상으로 두었습니다. 고정 대상과 EKS 주문 Pod를 각각 headless Service 이름으로 노출하고, 관리형 Envoy의 `STRICT_DNS` 클러스터 하나에 두 이름을 넣었습니다. 새 Pod가 Ready가 되면 Kubernetes가 EndpointSlice를 갱신하고 DNS가 그 IP를 반환합니다. Envoy는 2초 간격으로 이름을 다시 조회하도록 설정했습니다. 두 측정 사이에 바꾼 것은 EKS Deployment의 replicas뿐입니다. [Envoy STRICT_DNS](https://www.envoyproxy.io/docs/envoy/latest/intro/arch_overview/upstream/service_discovery) · [Kubernetes headless Service](https://kubernetes.io/docs/concepts/services-networking/service/#headless-services)

핵심 설정은 아래와 같습니다. 라우트는 `cluster: hybrid-demo` 하나를 가리키고, 개별 사이트 가중치는 선언하지 않습니다. 실제 실험에서는 EKS의 CoreDNS를 사용할 리졸버도 명시했습니다.

```yaml
name: hybrid-demo
type: STRICT_DNS
lb_policy: ROUND_ROBIN
dns_lookup_family: V4_ONLY
dns_refresh_rate: 2s
connect_timeout: 2s
load_assignment:
  cluster_name: hybrid-demo
  endpoints:
    - lb_endpoints:
        - endpoint:
            address:
              socket_address: {address: fixed-site.order.svc.cluster.local, port_value: 8080}
        - endpoint:
            address:
              socket_address: {address: elastic-site.order.svc.cluster.local, port_value: 8080}
```

| 동일한 Envoy 설정으로 120건씩 요청 | 고정 대상 | EKS 합계 | EKS Pod별 실제 응답 |
| --- | ---: | ---: | --- |
| 고정 1개 + EKS Ready Pod 1개 | 60 | 60 | 60 |
| 고정 1개 + EKS Ready Pod 3개 | 30 | 90 | 30 · 30 · 30 |

두 표본 모두 120건이 성공했습니다. EKS 응답의 Pod 이름과 Ready EndpointSlice 명단도 일치했습니다. 위의 50개·300개 표는 같은 조건을 가정한 계산값이고, 실제 두 사이트 간 링크나 350개 Pod의 DNS 응답을 시험한 수치는 아닙니다. [실측 설정·EndpointSlice 명단·요청별 결과](/assets/data/elb-envoy-tokyo/hybrid-dns.json)

이 방식을 실제 두 사이트에 쓰려면 Envoy가 양쪽 Pod IP에 접속할 수 있고, 두 DNS 이름을 구분해서 해석하며 각 사이트의 전체 Ready 주소를 받아야 합니다. 일반 Service의 ClusterIP 한 개나 사이트별 게이트웨이 주소 한 개씩만 보이면 이 공식으로 분배되지 않습니다. 기존 멀티클러스터 컨트롤 플레인이 EDS로 개별 엔드포인트를 전달하는 환경에서도 같은 원리를 적용할 수 있습니다. Pod 처리 용량이 서로 다르거나 locality 우선·장애 조치 정책을 쓰면 요청 비중도 달라집니다.

반대로 **사이트별 트래픽 비율 자체를 운영자가 통제**하려면 두 업스트림을 유지하고 `weighted_clusters`를 사용합니다. 예를 들어 EKS 비용 때문에 트래픽을 최대 60%로 제한한다면 300개까지 확장해도 85.7%를 보내면 안 됩니다. 고정 비율이면 선언한 값만으로 충분합니다. 가변 정책이면 두 클러스터의 Ready EndpointSlice를 감시해 `min(Ready 비율, 60%)`를 계산하는 컨트롤러를 둘 수 있습니다. Gateway API를 쓰면 이 컨트롤러는 `HTTPRoute.backendRefs.weight`를 갱신하고 xDS 생성은 Envoy Gateway에 맡깁니다. 직접 구성한 `weighted_clusters`에서는 RDS 가중치를 갱신합니다. 관측이 끊겼을 때 마지막 유효 값을 유지할지, 실제 장애일 때 어느 사이트로 넘길지도 그 정책의 일부입니다.

기존 게이트웨이에서 **어떤 정책을 유지할지**가 전환 기준입니다. 외부 요청의 경로 재작성·가중치 라우팅·로컬 버스트 제한이 주된 용도라면, 이번 실험처럼 Envoy 데이터 플레인을 클러스터 밖으로 옮길 수 있습니다. 릴리스마다 Pod 레이블로 카나리를 고르거나 메시 mTLS 신원과 네임스페이스 정책까지 사용한다면, 그 의미를 관리할 컨트롤 플레인과의 통합이 마이그레이션의 중심입니다. 도쿄에서는 인입 데이터 경로와 istiod에 연결된 두 사이드카의 인바운드 제한을 검증했습니다. 워크로드 간 STRICT mTLS와 인증서 교체는 별도 검증 항목입니다.
