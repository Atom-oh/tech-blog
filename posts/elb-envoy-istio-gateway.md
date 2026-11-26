---
title: "ELB for Envoy가 Istio Ingress Gateway를 대체할 수 있을까?"
date: 2026-11-26
description: "도쿄에서 CloudFront와 내부 NLB 뒤에 관리형 Envoy를 두고 EKS Pod, EC2, ECS로 요청을 보냈습니다. 경로 재작성·가중치 라우팅·로컬 요청 제한을 실측하고 Istio 정책과 xDS 운영의 차이를 짚습니다."
tags: envoy, istio, elb, eks, cloudfront
---

도쿄의 EKS Pod, EC2 인스턴스, ECS 태스크가 모두 같은 URL 아래에 있다면, 클러스터 안에 Istio Ingress Gateway Pod를 계속 운영해야 할까요? AWS가 출시한 **ELB for Envoy**는 제가 작성한 Envoy 설정을 AWS가 운영하는 프록시에서 실행합니다. 이번에는 세 백엔드가 실제로 받은 경로를 응답하게 해, 어느 계층이 요청을 바꿨는지 확인했습니다. [AWS 출시 공지](https://aws.amazon.com/ko/about-aws/whats-new/2026/11/elastic-load-balancing-for-envoy-generally-available/)

**인입 트래픽의 경로 선택과 재작성이 목적이라면 게이트웨이 Pod를 옮길 수 있습니다.** 다만 기존 `VirtualService`와 `DestinationRule`을 누가 읽을지는 별개의 결정입니다. 예를 들어 “`version: canary`인 Pod로 20%를 보낸다”에는 두 일이 들어 있습니다. Envoy는 두 업스트림 사이에서 요청을 나눌 수 있습니다. 어떤 Pod가 지금 canary이고 요청을 받을 준비가 됐는지 알아내는 일은 컨트롤 플레인의 몫입니다.

## 도쿄에서 요청 한 건이 지나가는 경로

```text
클라이언트
  └─ HTTPS · 서명 URL → CloudFront
       └─ HTTP · VPC 오리진 → 내부 NLB (TCP 80)
            └─ IP 타깃 2개 → 내부 ELB for Envoy (HTTP 8080)
                 ├─ /eks/ → EKS Fargate Pod
                 ├─ /ec2/ → EC2 인스턴스
                 └─ /ecs/ → ECS Fargate 태스크
```

CloudFront가 VPC 오리진으로 지원하는 리소스는 ALB, NLB, EC2입니다. 실습에서는 보안 그룹을 가진 **내부 IPv4 NLB**를 오리진으로 선택했습니다. CloudFront에서 NLB까지는 HTTP, NLB에서 Envoy까지는 TCP로 전달하고, 8080의 Envoy 리스너에서 처음으로 경로 규칙을 적용합니다. 뷰어 구간은 HTTPS이고 배포는 서명 URL을 요구합니다. 프라이빗 구간의 HTTP는 이번 실습의 선택입니다. [CloudFront VPC 오리진 요건](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/private-content-vpc-origins.html)

새 VPC에는 두 종류의 게이트웨이가 있습니다. 일반 IGW는 CloudFront VPC 오리진의 요구 사항이라 연결했습니다. 프라이빗 서브넷에는 IGW를 가리키는 IPv4 기본 경로가 없습니다. 별도의 **egress-only IGW**가 IPv6 기본 경로 `::/0`의 대상입니다. 이 실험에서 NLB·Envoy·백엔드 사이의 요청은 사설 IPv4를 사용했습니다. ECR API·레지스트리와 S3에는 VPC 엔드포인트를 통해 접근했습니다. [VPC 템플릿](/assets/data/elb-envoy-tokyo/network.yaml) · [워크로드 템플릿](/assets/data/elb-envoy-tokyo/workloads.yaml)

NLB는 CloudFront가 만든 VPC 오리진 보안 그룹에서 오는 80번 연결을 허용합니다. Envoy의 8080 인바운드는 NLB 보안 그룹에 열었고 EC2·ECS 백엔드에는 Envoy 보안 그룹에서 오는 8080 규칙을 두었습니다. EKS Fargate Pod의 클러스터 보안 그룹에도 같은 출발지를 허용했습니다. EKS 클러스터 보안 그룹에는 그 그룹 내부 통신을 허용하는 기본 규칙도 있습니다. Pod 관리에는 SSM으로 접속한 실습 EC2와 EKS의 프라이빗 API 엔드포인트를 사용했습니다. [CloudFront와 NLB 템플릿](/assets/data/elb-envoy-tokyo/edge.yaml) · [ECS 서비스 템플릿](/assets/data/elb-envoy-tokyo/ecs.yaml)

## 요청을 보내서 확인한 결과

2026년 11월 26일 도쿄 리전의 두 AZ에서 Envoy 1.40과 EKS 1.37을 사용했습니다. CloudFront에는 캐싱 비활성화 정책을 적용했습니다. EKS와 ECS는 같은 echo 이미지를 사용하고 환경 변수로 자신의 이름을 구별합니다. EC2의 HTTP 서버도 같은 형식으로 이름과 받은 경로를 돌려줍니다. [요청별 실측 JSON](/assets/data/elb-envoy-tokyo/observations.json) · [검증 스크립트](/assets/data/elb-envoy-tokyo/probe.py) · [컨테이너의 echo 서버](/assets/data/elb-envoy-tokyo/echo.go)

| CloudFront에 보낸 요청 | 뷰어가 받은 결과 | 확인한 동작 |
| --- | --- | --- |
| 서명 없는 `/readyz` | HTTP 403, `MissingKey` | CloudFront가 서명 누락을 거부 |
| 서명한 `/eks/probe` | HTTP 200, `{"backend":"eks","path":"/probe"}` | EKS Pod가 받은 경로에서 `/eks`가 사라짐 |
| 서명한 `/ec2/probe` | HTTP 200, `{"backend":"ec2","path":"/probe"}` | EC2에 같은 rewrite 적용 |
| 서명한 `/ecs/probe` | HTTP 200, `{"backend":"ecs","path":"/probe"}` | ECS 태스크에 같은 rewrite 적용 |
| 서명한 `/unmatched` | HTTP 404, 본문 없음 | Envoy의 마지막 직접 응답 규칙 적용 |
| 서명한 `/limited/{n}` 16건 | HTTP 200 **4건**, HTTP 429 **12건** | 429 응답 12건 모두 `x-lab-limit: local` |
| 서명한 `/split/{n}` 50건 | 모두 HTTP 200, **EC2 43건·ECS 7건** | 두 업스트림에 실제로 분산 |

**버킷을 2로 설정했는데 왜 4건이 통과했을까요?** `/limited/`에는 Envoy의 HTTP `local_ratelimit` 필터를 걸고 버킷 용량 2, 60초마다 토큰 2개 보충, 적용 비율 100%로 설정했습니다. 동시성 4로 보낸 요청 중 첫 2건을 넘겨 성공한 것이 보입니다. 기본 로컬 버킷은 Envoy 프로세스마다 공유됩니다. 여러 프록시를 거치는 이 경로에서 API 전체가 분당 두 건으로 제한된다고 설계하면 안 됩니다. 이번 응답에는 프로세스 ID가 없어 성공 건수로 프로세스 수를 확정할 수는 없습니다. [Envoy 로컬 요청 제한](https://www.envoyproxy.io/docs/envoy/latest/configuration/http/http_filters/local_rate_limit_filter)

**80 대 20은 요청마다 적용하는 가중치입니다.** 이번 표본에서 EC2는 86%, ECS는 14%를 받았습니다. 50건을 보낼 때마다 정확하게 40건과 10건이 되는 할당량은 아닙니다. `/split/` 규칙에는 rewrite를 넣지 않아 응답의 경로도 `/split/0`처럼 유지됐습니다.

**보안 그룹이 다른 경로도 확인했습니다.** 같은 VPC의 실습 EC2에서 로컬 echo는 200이었고, 내부 NLB(80), Envoy(8080), EKS Pod(8080), ECS 태스크(8080)로 각각 직접 연결하면 모두 3초 타임아웃이었습니다. 동일한 백엔드에 서명한 CloudFront URL로 접근했을 때는 위 표처럼 성공했습니다. 이 검사는 해당 EC2 보안 그룹에서 시도한 경로에 한정됩니다.

**라우트 갱신도 같은 URL에서 관찰했습니다.** `/revision`의 서명된 응답은 12:31:56 UTC에 `v1`이었습니다. 준비된 세 주소와 새 응답 문자열을 ZIP에 담아 CloudFormation 변경 세트를 실행하자 12:32:29 UTC에는 `v2`가 보였습니다. 두 응답 모두 `Miss from cloudfront`였고 변경 세트는 `Proxy.ConfigurationArchive` 한 속성의 수정만 표시했습니다. 두 시각은 요청을 보낸 시점이며 전체 프록시의 수렴 시간이나 연결 무중단을 측정한 값은 아닙니다. [프록시 설정 업데이트 API](https://docs.aws.amazon.com/elasticloadbalancing/latest/elbproxy/update-proxy-configuration.html)

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

기존 Istio CRD를 그대로 유지하려면 이를 해석하는 istiod 등의 구성 경로와 관리형 프록시 사이의 연결, 신원 발급, SDS, 버전 호환성을 별도로 검증해야 합니다. AWS가 문서화한 EKS 통합은 Envoy Gateway에 `elb-envoy-remote-infra-provider`를 붙이는 방식입니다. 이 컨트롤러가 관리형 프록시를 만들고 Envoy Gateway가 xDS를 제공합니다. 출시 시점의 통합 API는 `v1alpha1`이며 이 통합에서는 글로벌 rate limiting을 지원하지 않습니다. [EKS 통합의 동작과 제약](https://docs.aws.amazon.com/elasticloadbalancing/latest/elbproxy/kubernetes-overview.html)


## 운영에서 누가 주소를 따라갈 것인가

이 구성에는 별도로 갱신할 주소 목록이 **두 개** 있습니다. 첫째, NLB에서 관리형 Envoy로 가는 타깃입니다. NLB는 `instance`, `ip`, `alb` 타깃을 지원합니다. 이번에는 VPC 안에서 Envoy DNS의 A 레코드 두 개를 조회해 **IP로 등록**했고 두 타깃 모두 `/readyz` 검사에서 `healthy`가 됐습니다. NLB에는 DNS 이름이나 Envoy 리소스와의 연결 정보가 들어 있지 않습니다. DNS 응답이 달라질 때 새 주소를 등록하고 헬스 체크 통과 뒤 이전 주소를 빼는 작업은 별도로 설계해야 합니다. 실험 중 주소 교체나 장애 전환을 유발하지는 않았습니다. [NLB 타깃 유형](https://docs.aws.amazon.com/elasticloadbalancing/latest/network/load-balancer-target-groups.html) · [관리형 프록시의 네트워크 구조](https://docs.aws.amazon.com/elasticloadbalancing/latest/elbproxy/how-proxies-work.html)

둘째, Envoy에서 백엔드로 가는 주소입니다. 이번에는 Ready 상태의 Pod IP, EC2의 사설 IP, RUNNING 상태의 ECS 태스크 IP를 각각 `STATIC` 클러스터에 넣었습니다. 다음 배포에서 Pod나 태스크가 새 IP를 받으면 파일을 다시 만들어야 합니다. xDS 서버를 붙인다면 이 두 번째 문제를 EDS로 처리할 수 있습니다. 그 서버가 Kubernetes의 EndpointSlice나 ECS의 서비스 발견 정보를 읽고, 준비된 엔드포인트를 골라 전달하도록 구성해야 합니다. 이때도 첫 번째 문제인 NLB의 IP 타깃 관리는 별개입니다.

기존 게이트웨이에서 **어떤 정책을 유지할지**가 전환 기준입니다. 외부 요청의 경로 재작성·가중치 라우팅·로컬 버스트 제한이 주된 용도라면, 이번 실험처럼 Envoy 데이터 플레인을 클러스터 밖으로 옮길 수 있습니다. 릴리스마다 Pod 레이블로 카나리를 고르거나 메시 mTLS 신원과 네임스페이스 정책까지 사용한다면, 그 의미를 관리할 컨트롤 플레인과의 통합이 마이그레이션의 중심입니다. 여기서 검증한 대상은 외부 인입 경로이며, istiod 연결과 서비스 간 메시 트래픽은 실험 범위 밖입니다.

## 같은 검증을 실행하려면

1. 도쿄의 두 AZ에 네트워크와 워크로드를 생성합니다. ECR에 올릴 echo 실행 파일은 Linux/amd64로 빌드하고 실습용 Dockerfile과 같은 빌드 디렉터리에 둡니다. ECS Fargate 서비스를 띄운 뒤 EKS Fargate 프로파일이 `ACTIVE`가 되면 프라이빗 EKS API에 접근할 수 있는 실습 EC2에서 Pod를 만듭니다. Pod의 `ready`가 참이 된 뒤 주소를 읽습니다. [컨테이너 빌드 파일](/assets/data/elb-envoy-tokyo/Dockerfile) · [Pod 생성·상태 조회](/assets/data/elb-envoy-tokyo/pod.py)
2. 실습용 RSA 키 쌍의 공개 키로 CloudFront 키 그룹을 만들고 VPC 오리진 생성 후 NLB 보안 그룹에 CloudFront 서비스 관리 보안 그룹을 80번 소스로 추가합니다. 서명용 개인 키는 테스트 클라이언트에 둡니다. 먼저 `python3 render_proxy.py --revision v1 > proxy.json`으로 초기 템플릿을 생성해 프록시 스택에 적용합니다. Envoy가 생성되면 VPC 내부에서 DNS를 조회해 그 사설 주소들을 NLB 타깃 그룹에 8080번으로 등록합니다.
3. 서명한 `/revision`에서 `v1`을 읽은 뒤, 아래 첫 번째 명령이 만든 템플릿으로 같은 스택을 갱신합니다. 스택의 파라미터는 두 프라이빗 서브넷 ID(`PrivateA`, `PrivateB`)와 보안 그룹 ID(`ProxySG`, `NlbSG`)입니다. 두 NLB 타깃이 healthy이고 서명한 `/revision`에서 원하는 버전이 보이면 전체 요청을 보냅니다. 검증 스크립트에는 Python의 `cryptography` 패키지가 필요합니다.

```bash
python3 render_proxy.py \
  --eks "$POD_IP" --ec2 "$INSTANCE_IP" --ecs "$TASK_IP" \
  --revision v2 > proxy.json

python3 probe.py --hostname "$CF_DOMAIN" --key-id "$CF_PUBLIC_KEY_ID" \
  --private-key "$VIEWER_KEY_FILE" --revision-only --expect-revision v2

python3 probe.py --hostname "$CF_DOMAIN" --key-id "$CF_PUBLIC_KEY_ID" \
  --private-key "$VIEWER_KEY_FILE" --expect-revision v2
```
