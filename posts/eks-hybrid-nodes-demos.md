---
title: "EKS Hybrid Nodes를 직접 돌려 봤습니다: gateway를 아껴 써야 하는 이유부터 SNAT 없는 연결, PrivateLink, 링크 단절까지"
date: 2026-10-19
description: "온프렘을 시뮬레이션한 클러스터에서 Hybrid Nodes gateway, bursting, GPU 추론, ALB, Pod IP 가시성, PrivateLink, CoreDNS 단절을 실측한 기록"
tags: eks, hybrid-nodes, cilium, networking
---

# EKS Hybrid Nodes를 직접 돌려 봤습니다: gateway를 아껴 써야 하는 이유부터 SNAT 없는 연결, PrivateLink, 링크 단절까지

<p class="lead">EKS Hybrid Nodes Deep Dive 세션의 5부(라이브 데모)를 준비하며 실제 클러스터에서 직접 테스트했습니다. 이 글에는 세션에서 보여 드린 영상 다섯 편의 내용에 더해, 발표에서 다 설명하지 못한 설정과 실패 원인까지 정리했습니다. 수치는 모두 2026년 10월 15일부터 19일 사이에 측정했습니다. 시나리오마다 1~3회만 측정했으므로 작은 차이는 오차로 봐야 합니다.</p>

<div class="callout bad"><strong>시뮬레이션 환경입니다</strong>온프렘 서버와 VPN이 없어서, 두 번째 VPC를 Transit Gateway(TGW)로 연결하고 그 안의 EC2를 hybrid node로 등록했습니다. EC2를 hybrid node로 사용하는 방식은 프로덕션에서 지원되지 않습니다. 노드가 조인하면 hybrid node 요금도 부과됩니다. 실제 구성(온프렘 서버, DX 또는 VPN)에서는 노드 CIDR과 호스트만 바뀌지만, 지연과 처리량은 다시 측정해야 합니다.</div>

<div class="callout warn"><strong>먼저 결론: 중요 서비스에는 Hybrid Nodes gateway를 적극 권하지 않습니다</strong>gateway는 클라우드에서 hybrid Pod로 가는 트래픽 전부가 지나는 데이터 경로에 들어가고, 한 번에 리더 하나만 경로를 가집니다. 리더가 바뀌는 3~5초 동안 실제로 요청이 실패했습니다(초당 1회 호출 기준 60회 중 1~2회, ALB 경로 191건 중 1건). gateway 노드를 교체하거나 업그레이드할 때마다 같은 공백이 생길 수 있습니다. 그래서 출발점은 <strong>Pod CIDR을 온프렘에서 라우팅 가능하게 만드는 구성</strong>(VXLAN 유지, masquerade 끔, Cilium BGP로 광고)입니다. 이 구성은 AWS 지원 범위 안이고(AWS 네트워킹 문서도 Pod CIDR을 라우팅 가능하게 만드는 방법으로 Cilium BGP를 권장합니다) 데이터 경로에 gateway가 없습니다. 다만 이 구성 자체는 이번 시험에서 측정하지 않았으므로, 도입 전에 직접 확인해야 합니다. gateway는 Pod CIDR을 온프렘 네트워크에 광고할 수 없을 때의 차선책으로 보고, 사용한다면 아래 2절의 조건을 갖춰야 합니다.</div>

<nav class="toc" markdown="1">

**목차**

1. [시험 환경](#env)
2. [데모 1: Hybrid Nodes gateway 리더 전환, 그리고 권하지 않는 이유](#demo1)
3. [데모 2: 클라우드로 넘칠 때와 줄일 때 (bursting과 pod-deletion-cost)](#demo2)
4. [데모 3: 온프렘 GPU에서 추론하고 클라우드에서 호출](#demo3)
5. [Istio와 gateway는 함께 쓸 수 있나](#istio)
6. [데모 4: ALB ip 타깃과 온프렘에서 들어오는 역방향](#alb)
7. [데모 5: SNAT 없는 Pod 연결과 PrivateLink](#conn)
8. [링크가 끊기면 hybrid Pod의 DNS는](#dns)
9. [자주 받은 질문](#faq)
10. [정리와 비용](#wrap)

</nav>

<h2 id="env">1. 시험 환경</h2>

| 구분 | 값 |
|---|---|
| 클러스터 | EKS 1.36, ap-northeast-2, 클라우드 system 노드 2대 |
| 클라우드 VPC | `10.0.0.0/16`, 클라우드 노드는 VPC CNI |
| 시뮬레이션 온프렘 VPC (remote node network) | `172.18.0.0/16`, hybrid 호스트 2대(m7i.xlarge, 4 vCPU), GPU 데모 때만 g6.xlarge 1대 |
| remote pod network | `172.17.0.0/16`, Cilium 1.18 (AWS 빌드) cluster-pool, 노드당 `/25` |
| Service CIDR | `172.16.0.0/16` |
| 노드 인증 | SSM hybrid activation, 호스트에는 공인 IP 없음, SSH는 EC2 Instance Connect Endpoint로만 |

보안 기준도 지켰습니다. 보안 그룹의 ingress는 인터넷에 개방하지 않았고, 인스턴스에는 공인 IP를 할당하지 않았습니다. 로드 밸런서는 모두 internal로 구성했습니다.

데모 1~4는 **VXLAN 오버레이 + Hybrid Nodes gateway** 위에서 돌렸습니다. gateway는 온프렘 네트워크에 Pod CIDR을 광고할 수 없을 때, 클라우드에서 hybrid Pod로 가는 경로를 대신 만들어 주는 구성 요소입니다. 데모 5와 DNS 단절 시험은 같은 클러스터를 **native routing(VXLAN 없음, masquerade 끔)**으로 바꾼 뒤 돌렸습니다. 두 모드의 차이는 7절에서 설명합니다.

<h2 id="demo1">2. 데모 1: Hybrid Nodes gateway 리더 전환, 그리고 권하지 않는 이유</h2>

<figure class="video"><video src="https://presentation.atomai.click/eks-hybrid-nodes-1h/media/v1-gateway-failover.mp4" poster="https://presentation.atomai.click/eks-hybrid-nodes-1h/media/v1-gateway-failover.jpg" controls preload="metadata" muted playsinline></video><figcaption>데모 1 녹화 영상 (2분 58초, 무음, 실제 터미널 녹화 포함)</figcaption></figure>

이 환경에서는 Hybrid Nodes gateway를 클라우드 VPC 안의 노드 2대에서 실행했습니다. 그중 리더 하나가 VPC route table의 `172.17.0.0/16`(remote pod network) 경로를 자신의 ENI로 변경합니다. 클라우드에서 hybrid Pod로 가는 트래픽은 이 리더를 지나 VXLAN으로 온프렘 노드에 전달됩니다. 리더가 사라지면 Lease를 넘겨받은 다른 gateway가 경로를 자기 ENI로 바꿉니다. gateway와 온프렘 노드 사이 구간은 VXLAN으로 감싸지므로, 그 구간의 방화벽에는 바깥 헤더의 gateway와 노드 IP만 보입니다.

클라우드 Pod에서 hybrid Pod를 1초에 한 번 호출하는 동안 리더 Pod를 삭제하는 방식으로 시험했습니다.

| 측정 | 결과 |
|---|---|
| 2026-10-15, 3회 | 리더 전환 3초, 5초, 5초. 60회 중 실패 1, 1, 2회 |
| 녹화 회차 (2026-10-18) | Lease 인계 5초, 60회 중 실패 2회, 경로 ENI가 새 리더 ENI로 바뀜 |

세 번째 측정에는 최소 권한 정책을 적용했습니다. gateway가 관리하는 route table에서만 경로를 변경하도록 IAM 권한을 제한해도 동작했습니다.

### 그래서 중요 서비스에는 권하지 않습니다

리더 전환은 자동으로 잘 됐습니다. 문제는 전환하는 몇 초 동안 요청이 실제로 실패했다는 점입니다.

- **데이터 경로에 들어갑니다.** 클라우드에서 hybrid Pod로 가는 트래픽(리전 Pod의 호출, ALB ip 타깃, control plane의 webhook 호출)이 전부 리더 gateway 노드를 지납니다. 리더는 한 번에 하나라서 처리량도 리더 노드 하나가 감당합니다. 이번 시험에서는 처리량 한계를 재지 않았습니다.
- **공백은 장애 때만 생기지 않습니다.** gateway 노드의 AMI 교체나 인스턴스 타입 변경처럼 리더 Pod가 옮겨 가는 순간마다 비슷한 공백이 생길 수 있습니다. 이번에 측정한 것은 리더 Pod를 정상적으로 삭제한 경우뿐입니다. 노드가 갑자기 중단되거나 AZ에 장애가 생기면 Lease가 반납되지 않아 만료를 기다려야 하므로, 공백은 3~5초보다 길 가능성이 큽니다(측정하지 않음).
- **재시도가 없는 클라이언트는 그대로 실패합니다.** 아래 Istio 시험에서는 sidecar가 재시도해 사용자에게 실패가 드러나지 않았습니다. 반면 메시가 없는 일반 클라이언트(ALB 시험의 busybox `wget`)에서는 실패가 그대로 드러났습니다.
- **Cilium L7을 함께 쓸 수 없습니다.** gateway는 `l7Proxy: false`를 요구합니다.

gateway를 꼭 써야 한다면 최소한 다음 조건을 갖춰야 합니다. 클라이언트와 서비스의 재시도와 타임아웃, gateway 노드의 다중 AZ 배치와 PodDisruptionBudget, 노드 교체 작업의 사전 공지와 트래픽이 적은 시간대 실행, 리더 전환과 경로 변경 시각의 모니터링이 필요합니다. 가능하다면 Pod CIDR을 BGP로 광고해 gateway 없이 통신하는 구성을 먼저 검토하는 편을 권합니다.

<h2 id="demo2">3. 데모 2: 클라우드로 넘칠 때와 줄일 때 (bursting과 pod-deletion-cost)</h2>

<figure class="video"><video src="https://presentation.atomai.click/eks-hybrid-nodes-1h/media/v2-bursting.mp4" poster="https://presentation.atomai.click/eks-hybrid-nodes-1h/media/v2-bursting.jpg" controls preload="metadata" muted playsinline></video><figcaption>데모 2 녹화 영상 (3분 36초, 무음, 실제 터미널 녹화 포함)</figcaption></figure>

온프렘 용량이 차면 Karpenter로 EC2 burst 노드를 띄우고, 부하가 줄면 Pod를 다시 온프렘에만 남기려는 요구가 많습니다. 용량이 부족할 때 클라우드로 확장하는 과정은 쉽습니다. 부하가 줄었을 때 축소하는 과정이 더 까다롭습니다. ReplicaSet은 스케일 인할 때 `controller.kubernetes.io/pod-deletion-cost`가 낮은 Pod, 같은 노드에 복제본이 많은 Pod, 최근에 만든 Pod 순으로 지웁니다. 위치는 보지 않으므로 온프렘 Pod가 먼저 지워질 수 있습니다.

세 시나리오를 같은 순서(3개, 15개, 12개로 예열, 10개, 3개)로 비교했습니다. Pod 요청량은 930m라 hybrid 노드 2대에 8개가 들어갑니다.

| 시나리오 | 정책 | Pod 10개일 때 (hybrid / 클라우드 / burst EC2) | 최종 3개 | burst 노드 정리 |
|---|---|---|---|---|
| A | 정책 없음 | 6 / 4 / 3 | hybrid 2, 클라우드 1 | 123초 |
| B | Kyverno가 hybrid Pod에 cost 1 | 8 / 2 / 4 | hybrid 3 | 118초 |
| C | Kyverno + Karpenter `podDeletionCostManagement` | 8 / 2 / 3 | hybrid 3 | 87초 |

- **A**에서는 온프렘 Pod가 먼저 지워졌습니다.
- **B**는 Kyverno가 Pod 바인딩 시점에 노드를 보고 hybrid Pod에 cost를 붙여 온프렘 Pod 8개를 모두 지켰습니다. 남은 클라우드 Pod 2개는 burst 노드 4대에 흩어졌습니다.
- **C**에서는 Karpenter(alpha 기능 게이트)가 비울 수 있는 burst 노드의 Pod에 음수 cost를 설정했습니다. 그 결과 남는 노드는 3대로 줄었고, 정리도 더 빨랐습니다. 다만 1회만 측정했으므로 B와 C의 차이는 반복 측정으로 확인해야 합니다.

Karpenter는 사용률이 낮아 중단할 수 있는 노드만 순위를 매기고 1~5분마다 다시 계산합니다. 모든 노드가 가득 찬 상태에서 바로 줄이면 기록된 cost가 없어 C가 B와 같아집니다. 이 때문에 데모에서는 12개로 예열한 뒤 기다렸습니다. 녹화 회차에서는 15개가 모두 Ready가 되기까지 35초, 3개로 줄인 뒤 burst NodeClaim이 모두 사라지기까지 약 104초가 걸렸습니다.

<div class="callout warn"><strong>주의</strong>pod-deletion-cost는 best-effort입니다. 노드 장애나 축출에는 적용되지 않습니다. Karpenter 게이트를 켜면 consolidation은 <code>pod-deletion-cost</code> 대신 <code>karpenter.sh/disruption-cost</code>를 읽고, 게이트를 끌 때는 Karpenter가 쓴 음수 값을 지워야 합니다.</div>

<h2 id="demo3">4. 데모 3: 온프렘 GPU에서 추론하고 클라우드에서 호출</h2>

<figure class="video"><video src="https://presentation.atomai.click/eks-hybrid-nodes-1h/media/v4-gpu.mp4" poster="https://presentation.atomai.click/eks-hybrid-nodes-1h/media/v4-gpu.jpg" controls preload="metadata" muted playsinline></video><figcaption>데모 3 녹화 영상 (2분 14초, 무음, 실제 터미널 녹화 포함)</figcaption></figure>

시뮬레이션 GPU 호스트(g6.xlarge, NVIDIA L4)를 hybrid node로 조인한 뒤 vLLM으로 작은 모델(`Qwen/Qwen2.5-0.5B-Instruct`)을 띄웠습니다. 클라우드 Pod에서는 ClusterIP Service를 통해 호출했습니다.

| 단계 | 2026-10-18 녹화 회차 |
|---|---|
| 드라이버와 nvidia-container-toolkit 설치 | 2분 59초 (재부팅 포함) |
| nodeadm 설치부터 조인 | 50초 |
| vLLM `v0.19.1` 이미지 다운로드 (9.6 GB, NAT 경로) | 4분 35초 |
| apply부터 Ready | 7분 14초 |
| GPU 노드 | taint `nvidia.com/gpu=Exists:NoSchedule`, allocatable `nvidia.com/gpu: 1` |
| inference Pod | 컨테이너 1개, `istio-proxy` 없음, `hostNetwork=false`, Pod IP `172.17.1.161` |
| 클라우드 Pod 호출 | 응답 1초 미만 |

AWS 블로그 예시는 드라이버와 툴킷이 OS 이미지에 들어 있다고 가정합니다. 이번에는 준비 스크립트로 드라이버(`580` 계열)와 툴킷을 설치하고, `nodeadm init` 뒤 `nvidia-ctk runtime configure --runtime=containerd --set-as-default`를 실행했습니다. 실시간 시연 중에 이미지와 모델을 내려받으면 7분 넘게 걸립니다. 미리 받아 두고 호출만 보여 주는 편을 권합니다.

<h2 id="istio">5. Istio와 gateway는 함께 쓸 수 있나</h2>

gateway를 사용하려면 Cilium에 `l7Proxy: false`를 설정해야 하므로, 같은 Cilium 구성에서는 자체 L7 기능(L7 정책, Ingress, Gateway API)을 쓸 수 없습니다. Istio는 별도의 Envoy로 L7을 처리하기 때문에 sidecar 모드에서 확인했습니다(Istio 1.30.6, istiod는 클라우드 노드).

| 확인 | 결과 (2026-10-17) |
|---|---|
| hybrid 노드 2대를 포함한 Pod 4개 | 모두 2/2 |
| 클라우드와 hybrid 양방향 호출, mTLS | 200, 메시 밖 Pod의 평문 호출 거부 |
| AuthorizationPolicy | `/admin/users` 403 |
| gateway 리더를 지우는 동안 | 1,800건 중 1,800건 성공 |

같은 리더 전환에서 1,800건이 모두 성공한 이유는 Istio sidecar가 실패한 요청을 재시도했기 때문으로 보입니다. 데모 1의 일반 클라이언트에서는 같은 전환 중 요청이 실패했습니다. 메시가 있으면 공백이 사용자에게 드러나지 않을 뿐, 공백 자체가 없어지는 것은 아닙니다.

지연(sidecar에서 sidecar, 순차 300회, p50 / p90 / p99, ms)은 클라우드에서 클라우드 1.32 / 1.87 / 1.99, 클라우드에서 hybrid 2.46 / 2.90 / 3.00, hybrid에서 hybrid 0.62 / 0.93 / 1.00, hybrid에서 클라우드 2.11 / 2.77 / 2.92였습니다. gateway를 건너면 약 1ms가 더해집니다. ambient 모드와 gateway 처리량 한계는 확인하지 않았습니다.

<h2 id="alb">6. 데모 4: ALB ip 타깃과 온프렘에서 들어오는 역방향</h2>

<figure class="video"><video src="https://presentation.atomai.click/eks-hybrid-nodes-1h/media/v3-alb-reverse.mp4" poster="https://presentation.atomai.click/eks-hybrid-nodes-1h/media/v3-alb-reverse.jpg" controls preload="metadata" muted playsinline></video><figcaption>ALB 데모 녹화 영상 (3분 12초, 무음, 실제 터미널 녹화 포함)</figcaption></figure>

확인하고 싶은 것은 두 가지였습니다. hybrid Pod IP를 ALB `target-type: ip`로 등록했을 때 Pod IP가 바뀌면 누가 타깃을 갱신하는지, 그리고 온프렘에서 LB로 들어오는 트래픽이 어떤 경로를 거치는지입니다. ALB는 internal로 구성했고, AWS Load Balancer Controller 3.6.0을 사용했습니다.

| 테스트 | 결과 (2026-10-18 02:11 측정) |
|---|---|
| 등록 | hybrid Pod 2개가 ip 타깃으로 healthy. VPC 밖 IP라 AZ는 `all` |
| 리전 클라이언트에서 ALB로 hybrid Pod | 20/20 (ALB, VPC 경로, gateway, VXLAN) |
| 부하 중 rollout restart | 새 Pod IP로 54초에 수렴, 276건 중 실패 0 |
| 부하 중 gateway 리더 삭제 | 인계 4초, 191건 중 실패 1건 (재시도 없는 클라이언트) |
| 온프렘 호스트에서 internal ALB로 클라우드 Pod | 20/20, gateway를 거치지 않음 |
| 온프렘 호스트에서 internal ALB로 hybrid Pod | 20/20, 그러나 온프렘, AWS, 온프렘으로 도는 hairpin |
| 온프렘 호스트에서 hybrid NodePort로 클라우드 Pod | 실패. 노드 IP로 SNAT된 뒤 underlay로 가고, 클라우드 노드 SG가 이 대역에 443만 허용 |

타깃 갱신은 Load Balancer Controller가 담당합니다. gateway는 VPC 밖 Pod IP로 가는 경로만 제공합니다. ALB 앞단이 아무리 안정적이어도 gateway 리더 전환 중에는 요청이 실패했습니다(191건 중 1건). 중요 서비스를 hybrid Pod에서 ALB로 노출한다면, Pod CIDR을 라우팅 가능하게 만들어 ALB에서 TGW나 DX로 바로 가는 경로를 먼저 검토해야 합니다. 녹화 회차에서도 재시작 50초 뒤 새 Pod IP만 healthy로 남았고, 부하 174건 중 오류는 0이었습니다. 온프렘 클라이언트가 internal ALB를 쓰려면 온프렘 DNS가 ALB 이름을 private IP로 풀어야 합니다(Route 53 Resolver inbound endpoint). AWS 문서도 리전에서 오는 트래픽은 ALB로, 온프렘에서 오는 트래픽은 온프렘 쪽 Ingress나 LB로 받으라고 권장합니다.

<h2 id="conn">7. 데모 5: SNAT 없는 Pod 연결과 PrivateLink</h2>

<figure class="video"><video src="https://presentation.atomai.click/eks-hybrid-nodes-1h/media/v5-connectivity.mp4" poster="https://presentation.atomai.click/eks-hybrid-nodes-1h/media/v5-connectivity.jpg" controls preload="metadata" muted playsinline></video><figcaption>데모 5 녹화 영상 (2분 32초, 무음, 실제 터미널 녹화 포함)</figcaption></figure>

"방화벽이 온프렘 Pod를 노드 IP가 아니라 Pod IP로 구분해야 한다"는 요구사항을 받았습니다. 기본 구성에서는 Pod 트래픽이 클러스터 밖으로 나갈 때 Cilium이 노드 IP로 masquerade하고, hybrid 노드 사이는 VXLAN 바깥 헤더(노드 IP)로 감싸지므로 방화벽은 Pod IP를 볼 수 없습니다.

### 지원 범위부터 확인

[CNI 구성 문서](https://docs.aws.amazon.com/eks/latest/userguide/hybrid-nodes-cni.html)의 지원 기능 표에서 AWS가 지원하는 Cilium Networking Mode는 **VXLAN encapsulation뿐**입니다. 같은 문서는 masquerade를 끌 수 있고, 그러면 Pod CIDR이 온프렘 네트워크에서 라우팅 가능해야 하며, 이를 위해 Cilium BGP Control Plane을 권장한다고 설명합니다.

| 구성 | Pod IP가 보이는 구간 | AWS 지원 | gateway |
|---|---|---|---|
| VXLAN + masquerade (기본) | 없음 | 지원 | 사용 가능 |
| VXLAN + masquerade 끔 + BGP (개념, 이번에 측정하지 않음) | 클라우드와 온프렘 사이, 온프렘 서버 | 지원 | 필요 없음 (Pod CIDR이 라우팅되므로) |
| native routing + masquerade 끔 (이번 시험) | 위 구간과 hybrid 노드 사이 | **지원 범위 밖** | 사용 불가 (VTEP 필요) |

먼저 검토할 구성은 표의 두 번째 줄입니다. AWS 지원 범위 안에서 gateway를 데이터 경로에서 뺄 수 있고, 설계상 클라우드와 온프렘 사이의 방화벽에서 Pod IP를 볼 수 있습니다(이번에 측정하지 않은 구성이므로 도입 전에 직접 확인해야 합니다). 이번 시험에서는 hybrid 노드 사이 구간까지 확인하기 위해 표의 세 번째 줄 구성으로 테스트했습니다.

### 바꾼 설정

```yaml
# Cilium values (기본 values 위에 덧씌움)
routingMode: native
ipv4NativeRoutingCIDR: "172.17.0.0/16"
autoDirectNodeRoutes: false     # 호스트가 서로 다른 서브넷, 온프렘 라우터가 전달
enableIPv4Masquerade: false
vtep:
  enabled: false
```

- 온프렘 라우터에는 노드별 Pod 블록(`/25`)을 해당 노드로 보내는 경로가 필요합니다. 시험에서는 온프렘 VPC route table에 정적 경로를 넣었고, 운영 환경이라면 Cilium BGP로 광고합니다.
- 클라우드 쪽에서는 route table과 TGW에 `172.17.0.0/16`을 온프렘 방향으로 보내는 경로를 넣고, 호스트의 source/destination check를 껐습니다.
- VPC CNI: `AWS_VPC_K8S_CNI_EXCLUDE_SNAT_CIDRS=172.17.0.0/16,172.18.0.0/16`. 클라우드 Pod도 온프렘으로 갈 때 Pod IP를 유지합니다.
- Cilium chart는 ConfigMap이 바뀌어도 agent를 재시작하지 않으므로 `rollout restart ds/cilium`이 필요합니다. 재시작 뒤 호스트 NIC 캡처에서 VXLAN 패킷은 0건이었습니다.

### 38개 연결 매트릭스

Pod에서 출발하는 검사는 HTTP 응답만 보지 않고 **서버가 기록한 출발지 주소**를 확인했습니다(DNS와 API 검사는 응답만, control plane 검사는 EKS 관리 ENI가 출발지, 인터넷은 NAT 주소가 기준). nginx 서버에서는 접근 로그의 `$remote_addr`로 출발지 주소를 확인했고, Kubernetes 밖 서버에서는 받은 peer 주소를 그대로 돌려주는 `socat`을 사용했습니다.

| 구간 | 항목 | 결과 |
|---|---|---|
| Pod 간 직접 (같은 hybrid 노드, hybrid 노드 사이, hybrid와 클라우드 양방향, 클라우드 사이 기준선) | 7 | 서버가 본 출발지 = 클라이언트 Pod IP |
| ClusterIP Service | 3 | 목적지만 바뀌고 출발지 Pod IP 유지 |
| DNS (hostNetwork Pod 포함), Kubernetes API | 6 | 이름 해석, `/livez` 200, SA 토큰 403 |
| control plane에서 hybrid로 | 3 | API server proxy, exec와 logs, metrics-server. proxy 출발지는 클라우드 VPC의 EKS 관리 ENI |
| 쿠버네티스 밖 온프렘 서버 | 2 | 서버가 172.17.x, 10.0.x Pod IP를 받음 |
| 인터넷 | 3 | 모든 Pod가 클라우드 NAT 하나로 나감 |
| Cilium `fromCIDR` 정책, 32 MiB 양방향 전송, ALB 타깃 | 6 | 정책 동작, sha256 일치, healthy |
| PrivateLink | 8 | 아래 |

최종 결과는 38개 모두 PASS였습니다. 첫 실행에서는 세 개가 실패했습니다.

1. **hostNetwork Pod의 DNS:** hostNetwork Pod가 노드 IP(172.18.x)로 CoreDNS에 질의했지만, 클러스터 SG에는 이 대역의 53번 포트를 허용하는 규칙이 없었습니다.
2. **클라우드 Pod에서 온프렘 서버로 갈 때 SNAT:** VPC CNI 제외 목록에 Pod 대역만 있고 온프렘 노드 대역이 없었습니다.
3. **익명 `/version` 401:** EKS 1.36은 익명 요청을 health endpoint에만 허용합니다. `/livez`로 확인합니다.

### 온프렘 Pod에서 PrivateLink로 AWS API

클라우드 VPC에 STS와 S3 interface endpoint(private DNS)를 만들었습니다. hybrid Pod는 CoreDNS를 거쳐 VPC resolver로 이름을 해석하므로, `sts`, `s3` 이름이 endpoint ENI의 10.0.x 주소로 해석됐습니다. IRSA에서는 Pod가 직접 STS를 호출해 토큰을 교환하므로, 이 호출부터 endpoint를 거칩니다.

| 확인 | 결과 |
|---|---|
| hybrid Pod: `sts get-caller-identity` (IRSA) | assumed-role ARN |
| hybrid Pod: PutObject, 클라우드 Pod: GetObject | 같은 내용. 버킷 정책이 `aws:SourceVpce`로 endpoint 밖을 막으므로 endpoint를 탔다는 증거 |
| endpoint SG의 443을 클라우드 VPC(10.0.0.0/16)와 hybrid Pod 대역(172.17.0.0/16)에만 열고(노드 대역 172.18.0.0/16 제외) 같은 hybrid 노드에서 호출 | Pod IP는 HTTP 302, hostNetwork(노드 IP)는 타임아웃 |
| 같은 역할로 endpoint 밖(작업 PC)에서 S3 | 거부 |

같은 노드에서 출발한 트래픽이지만 Pod는 통과했고 hostNetwork는 차단됐습니다. 이 결과로 **AWS 쪽 SG로도 온프렘 Pod를 Pod 대역으로 구분할 수 있다**는 점을 확인했습니다. SNAT이 없다는 가장 직접적인 증거이기도 합니다.

<div class="callout warn"><strong>S3 interface endpoint 생성 실패</strong>gateway endpoint가 없는 VPC에서 S3 interface endpoint를 private DNS와 함께 만들자 "To set PrivateDnsOnlyForInboundResolverEndpoint to true, the VPC must have a Gateway endpoint"로 실패했습니다. <code>DnsOptions.PrivateDnsOnlyForInboundResolverEndpoint: AllResolvers</code>를 지정해 해결했습니다. S3 gateway endpoint는 온프렘에서 쓸 수 없으므로 온프렘 Pod용으로는 interface endpoint가 필요합니다.</div>

<h2 id="dns">8. 링크가 끊기면 hybrid Pod의 DNS는</h2>

연결 매트릭스를 확인하다가 CoreDNS replica 두 개가 모두 같은 클라우드 노드에 배치된 것을 발견했습니다. DX나 VPN이 끊겼을 때 어떻게 동작할지 확인하기로 했습니다. TGW route table에 blackhole 경로(온프렘 노드와 Pod 대역의 `/17`, 스택의 경로보다 구체적이라 CloudFormation 리소스는 건드리지 않음)를 넣어 약 95초 동안 연결을 끊고, hybrid 노드마다 1초 간격으로 이름 해석과 호출을 기록했습니다. 경로 변경이 반영되는 데 약 17초가 걸렸습니다.

| CoreDNS 구성 | 단절 중 hybrid Pod 이름 해석 |
|---|---|
| 기본: 2개 모두 클라우드 노드 | **모두 실패** (경로 반영을 기다린 처음 약 17초를 뺀 단절 구간 전부, 43~44개 중 26개). 같은 Service를 ClusterIP로 부르면 200 |
| hybrid 노드 zone label + 3개 + `PreferSameZone` | 절반가량 실패 (76~80개 중 38~44개) |
| 4개 + `PreferClose` + zone 분산 강제 + Cilium 재시작 | **실패 0** (96~97개 모두 성공) |

기본 구성에서는 온프렘 안의 통신은 살아 있는데 이름을 못 풀어 서비스가 멈춥니다. AWS 문서([Configure CoreDNS replicas](https://docs.aws.amazon.com/eks/latest/userguide/hybrid-nodes-webhooks.html))는 mixed mode 클러스터에서 hybrid 노드와 클라우드 노드에 CoreDNS를 각각 1개 이상 두고, Service Traffic Distribution으로 가까운 replica를 쓰라고 권장합니다. 문서의 예시는 kube-dns Service에 `trafficDistribution: PreferClose`를 넣고, Cilium은 `loadBalancer.serviceTopology: true`로 켠 뒤 agent를 재시작하라고 안내합니다. Kubernetes 1.35부터 `PreferClose`가 deprecated 별칭이라는 점을 보고, 저는 새 이름인 `PreferSameZone`으로 바꿨습니다. 이 변경이 hybrid 쪽 실패의 직접 원인이었습니다. Cilium을 재시작한 뒤에도 `PreferSameZone`에서는 BPF 백엔드가 3개 그대로였지만, `PreferClose`로 바꾸자 곧바로 온프렘 2개만 남았습니다. 다만 실패가 0이 된 회차에서는 값 변경, Cilium 재시작, replica 증설(3개에서 4개)을 함께 적용했으므로 각 변경의 영향을 따로 측정하지는 못했습니다. 여기서 확인한 내용은 네 가지입니다.

1. **문서대로 `PreferClose`를 사용해야 합니다. Cilium 1.18은 `PreferSameZone`을 무시했습니다.** hybrid 노드의 ClusterIP 부하 분산은 kube-proxy가 아니라 Cilium BPF가 담당합니다(`kubeProxyReplacement: false`여도). `PreferSameZone`일 때도 EndpointSlice에는 zone hint가 있었지만 `cilium-dbg bpf lb list`에는 CoreDNS 백엔드가 모두 남아 있었고, `PreferClose`로 바꾸자 온프렘 백엔드만 남았습니다. 문서의 확인 방법(EndpointSlice의 hint)만으로는 이 차이가 드러나지 않습니다. hybrid 노드의 Cilium에서 백엔드 목록도 직접 확인해야 합니다.
2. **zone label은 조인할 때 지정해야 합니다.** 이번 시험에서는 label을 나중에 지정한 뒤 Cilium agent를 재시작했습니다. 재시작 없이도 반영되는지는 따로 확인하지 않았습니다. nodeadm 설정의 kubelet `--node-labels=topology.kubernetes.io/zone=<이름>`으로 조인할 때 label을 지정하면 이 문제를 피할 수 있습니다.
3. **클라우드 AZ에도 CoreDNS가 필요합니다.** 같은 zone에 CoreDNS가 없으면 전체 백엔드로 분산되므로, 단절 시 온프렘 CoreDNS로 간 질의가 실패합니다. 클라우드 AZ마다 하나, 온프렘에 하나 이상이 필요합니다. `preferred` anti-affinity만으로는 재시작 뒤 배치가 흐트러져, `topologySpreadConstraints`의 `DoNotSchedule`로 배치를 강제했습니다.
4. **온프렘 CoreDNS를 거친 호출이 PrivateLink를 우회했습니다.** 온프렘 CoreDNS는 질의를 노드 resolver(온프렘 DNS)로 전달하므로, STS와 S3가 공인 IP로 해석됐습니다. NAT를 통한 호출은 성공해 겉으로는 정상처럼 보였지만, S3는 버킷 정책에 막혀 매트릭스 4개가 FAIL이 됐습니다. 클라우드 VPC에 Route 53 Resolver inbound endpoint를 두고 CoreDNS에 `amazonaws.com` 블록을 추가해 해결했습니다. 실제 온프렘에서는 온프렘 DNS 서버의 조건부 전달 규칙이 같은 역할을 합니다.

최종 CoreDNS add-on 구성 값입니다.

```json
{
  "replicaCount": 4,
  "trafficDistribution": "PreferClose",
  "topologySpreadConstraints": [{
    "maxSkew": 1, "topologyKey": "topology.kubernetes.io/zone",
    "whenUnsatisfiable": "DoNotSchedule",
    "labelSelector": {"matchLabels": {"k8s-app": "kube-dns"}}
  }],
  "corefile": "...기본 .:53 블록...\namazonaws.com:53 {\n    errors\n    cache 30\n    forward . <inbound endpoint IP 1> <inbound endpoint IP 2>\n}"
}
```

`corefile`은 Corefile 전체를 바꾸므로 기본 `.:53` 블록을 그대로 포함해야 합니다. 이 구성을 적용한 뒤 연결을 다시 끊었을 때 hybrid Pod의 실패는 0이었습니다. 연결 매트릭스도 다시 38개 모두 PASS였습니다.

<h2 id="faq">9. 자주 받은 질문</h2>

### remote pod 대역과 remote node 대역을 같게 할 수는 없나요? SNAT을 안 하면 Pod IP가 그대로 보이니 라우팅되지 않나요?

같게 할 수 없습니다. [네트워킹 준비 문서](https://docs.aws.amazon.com/eks/latest/userguide/hybrid-nodes-networking.html)는 온프렘 노드 CIDR과 Pod CIDR이 RFC 1918(또는 RFC 6598) 대역 안에 있고, **서로, 그리고 VPC CIDR, Service CIDR과 겹치지 않아야** 한다고 요구합니다. [CNI 문서](https://docs.aws.amazon.com/eks/latest/userguide/hybrid-nodes-cni.html)도 Cilium의 Pod CIDR이 온프렘 노드 CIDR과 겹치면 안 된다고 적고 있습니다.

SNAT을 끄는 것과 Pod IP를 라우팅할 수 있는지는 별개의 문제입니다. SNAT을 끄면 패킷에는 Pod IP가 그대로 실립니다. 라우팅이 가능하려면 네트워크가 그 Pod IP로 가는 응답을 어느 노드로 보내야 하는지 알아야 합니다. 따라서 SNAT을 끄면 응답을 돌려보낼 경로가 반드시 필요합니다.

- **Pod IP는 노드 NIC에 붙은 주소가 아닙니다.** Cilium cluster-pool은 노드마다 Pod 블록(이 시험은 `/25`)을 나눠 주고, Pod는 노드 안의 별도 네트워크 네임스페이스에 있습니다. 라우터는 "172.17.0.128/25는 노드 172.18.38.77로" 같은 L3 경로로 찾아갑니다. Pod IP가 노드 서브넷 안에 있으면 라우터는 그 주소를 같은 링크에 있다고 보고 ARP를 보내는데, 대답할 NIC가 없어 통신이 안 됩니다.
- **주소가 겹칩니다.** 노드 서브넷 안의 주소를 Pod에 나눠 주면 다른 서버나 장비와 같은 IP를 받을 수 있습니다.
- **EKS가 두 대역을 구분해서 씁니다.** control plane은 remote node network로 kubelet(10250)에, remote pod network로 webhook 같은 Pod에 접속합니다. 클라우드 쪽 route table, TGW, SG 규칙도 두 대역을 따로 다룹니다.

방화벽 규칙을 단순하게 만드는 것이 목적이라면, 두 대역을 인접하게 잡아 상위 대역 하나로 묶을 수 있습니다. 예를 들어 노드 `10.80.0.0/16`, Pod `10.81.0.0/16`으로 잡으면 방화벽에서는 `10.80.0.0/15` 하나로 다룰 수 있습니다. 다만 노드 서브넷의 주소를 Pod에 직접 주는 방식(VPC CNI처럼 L2로 붙이는 방식)은 hybrid node의 지원 IPAM(Cilium cluster-pool)에 포함되지 않습니다.

### Hybrid Nodes gateway를 써야 하나요?

온프렘 네트워크에 Pod CIDR을 광고할 수 있다면(라우터와 BGP를 맺을 수 있거나 정적 경로를 넣을 수 있다면) 쓰지 않는 편을 권합니다. Cilium BGP Control Plane은 AWS 지원 기능이고, 클라우드 VPC route table에는 remote pod network를 DX나 VPN 방향으로 넣으면 됩니다. gateway는 네트워크 팀이 Pod CIDR 광고를 허용하지 않는 등 라우팅을 바꿀 수 없을 때 쓰는 차선책입니다. gateway를 쓰는 경우에도 2절의 조건(재시도, 다중 AZ, 노드 교체 절차, 모니터링)을 갖춰야 합니다. 리더 전환 공백을 견딜 수 없는 서비스는 클라우드나 온프렘 한쪽에서 처리가 끝나도록 배치해야 합니다.

### 실제 온프렘에서는 무엇이 달라지나요?

- 정적 경로 대신 Cilium BGP Control Plane으로 노드별 Pod 블록을 온프렘 라우터에 광고합니다. 정적 경로는 노드가 다시 조인해 Pod 블록이 바뀌면 틀어집니다.
- 온프렘 DNS 서버에 `amazonaws.com`(그리고 internal ALB 이름이 있는 영역)을 Route 53 Resolver inbound endpoint로 보내는 조건부 전달 규칙을 둡니다.
- MTU는 실제 환경에서 다시 확인해야 합니다. 시험 환경에서는 Pod MTU 9001, TGW 한도 8500이었고, 32 MiB TCP 전송과 8500바이트 DF ping에 문제가 없었습니다. DX나 VPN 경로, 그리고 MSS 조정이 없는 UDP는 별도로 검증해야 합니다.

<h2 id="wrap">10. 정리와 비용</h2>

권하는 순서를 다시 정리하면 이렇습니다. ① Pod CIDR을 BGP로 광고하고 masquerade를 끈 VXLAN 구성(지원 범위 안, gateway 없음, 이번에 측정하지 않았으니 도입 전 검증 필요), ② 라우팅을 바꿀 수 없을 때만 gateway(재시도와 노드 교체 절차 필수), ③ hybrid 노드 사이까지 Pod IP가 필요할 때 native routing(지원 범위 밖, 자체 지원 역량 필요).

| 확인한 것 | 남은 것 |
|---|---|
| gateway 리더 전환은 자동이지만 3~5초 공백과 요청 실패가 있음 | B와 C(Kyverno 대 Karpenter 게이트)의 반복 측정 |
| Kyverno로 스케일 인 시 온프렘 Pod 보호 | Istio ambient 모드 |
| 온프렘 GPU 추론을 클라우드에서 호출 | gateway 처리량 한계 |
| Istio sidecar와 gateway 공존 | hostNetwork와 NodePort 경로의 노드 SG 설계 |
| ALB ip 타깃 자동 갱신, 온프렘에서 internal ALB 진입 | 실제 온프렘 서버(DX, VPN)에서의 재검증 |
| native routing에서 SNAT 없는 연결 38개, PrivateLink | native routing은 AWS 지원 범위 밖 |
| 링크 단절 중 hybrid Pod DNS 실패 0 (구성 수정 뒤) | |

환경을 켜 둔 동안에는 EKS control plane, NAT gateway, TGW attachment, EC2, hybrid node vCPU, interface endpoint(개수와 AZ별 시간 요금, 처리 GB), Route 53 Resolver inbound endpoint(ENI별 시간 요금)에 대한 비용이 계속 발생합니다. 시험이 끝나면 바로 정리해야 합니다. 데모 저장소의 정리 스크립트는 Karpenter 노드, gateway가 만든 VPC 경로, SSM 등록, PrivateLink 스택, 단절 시험용 blackhole 경로까지 순서대로 삭제합니다.

<p class="refs"><strong>참고 자료</strong></p>

- [Configure CNI for hybrid nodes](https://docs.aws.amazon.com/eks/latest/userguide/hybrid-nodes-cni.html)
- [Prepare networking for hybrid nodes](https://docs.aws.amazon.com/eks/latest/userguide/hybrid-nodes-networking.html)
- [Configure webhooks for hybrid nodes (CoreDNS replicas)](https://docs.aws.amazon.com/eks/latest/userguide/hybrid-nodes-webhooks.html)
- [Configure add-ons for hybrid nodes](https://docs.aws.amazon.com/eks/latest/userguide/hybrid-nodes-add-ons.html)
- [Best practices for stability through network disconnections](https://docs.aws.amazon.com/eks/latest/best-practices/hybrid-nodes-network-disconnection-best-practices.html)
- [Configure Ingress for hybrid nodes](https://docs.aws.amazon.com/eks/latest/userguide/hybrid-nodes-ingress.html)
- [AWS PrivateLink for Amazon S3](https://docs.aws.amazon.com/AmazonS3/latest/userguide/privatelink-interface-endpoints.html)
- [Karpenter: pod deletion cost](https://karpenter.sh/preview/concepts/pod-deletion-cost/)
- 세션 자료: [Deep Dive 덱 목차](https://presentation.atomai.click/eks-hybrid-nodes/toc.html), [5부 라이브 데모](https://presentation.atomai.click/eks-hybrid-nodes/05-live-demo.html), [데모 구현 가이드](https://presentation.atomai.click/eks-hybrid-nodes/demo-guide.html)
