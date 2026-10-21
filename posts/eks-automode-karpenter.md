---
title: "EKS Auto Mode와 Karpenter 1.15를 나란히 돌려 봤습니다: 스케일 아웃, Balanced consolidation, Drift, Spot 중단"
date: 2026-10-21
description: "EKS 1.37에서 Auto Mode 클러스터와 직접 설치한 Karpenter 1.15.0 클러스터를 같은 조건으로 만들고 스케일 아웃, consolidation, Drift, Spot 중단을 실측한 기록"
tags: eks, karpenter, auto-mode, autoscaling
---

# EKS Auto Mode와 Karpenter 1.15를 나란히 돌려 봤습니다: 스케일 아웃, Balanced consolidation, Drift, Spot 중단

<p class="lead">EKS Auto Mode와 Karpenter 세션의 데모 영상을 만들면서, 같은 VPC에 EKS 1.37 클러스터 두 개를 만들어 같은 워크로드를 돌렸습니다. 하나는 EKS Auto Mode, 다른 하나는 Karpenter 1.15.0을 직접 설치한 클러스터입니다. 이 글에는 세션에서 보여 드린 영상 네 편의 내용과 함께, 영상에 담지 못한 설정과 구성 중에 막힌 지점을 정리했습니다. 영상 네 편의 측정은 모두 2026년 10월 20일 UTC 13시 50분부터 14시 25분 사이에 했고, 각 시나리오는 한 번(consolidation은 두 번)씩만 돌렸습니다. 숫자는 동작을 확인하는 용도로만 보시고, 성능 비교로 쓰지 마시기 바랍니다.</p>

<div class="callout warn"><strong>먼저 결론: 노드를 고르는 판단은 같고, 차이는 운영 책임과 기본값입니다</strong>같은 NodePool 조건과 같은 Pod 요청에 두 클러스터는 같은 인스턴스 유형을 같은 대수만큼 골랐습니다. Auto Mode 안에서도 판단하는 것은 Karpenter입니다. 실무에서 차이가 나는 부분은 두 가지였습니다. 첫째, <strong>EKS 1.37에서 consolidationPolicy를 비워 두면 Auto Mode는 <code>Balanced</code>, 직접 설치한 Karpenter는 <code>WhenEmptyOrUnderutilized</code>가 들어갑니다.</strong> 정책은 Git에 명시해 두는 편이 안전합니다. 둘째, Spot 중단 처리는 Auto Mode에서는 AWS가 맡고, 직접 운영하면 SQS interruption queue와 EventBridge 규칙을 팀이 구성합니다. 그리고 Karpenter 클러스터에서만 확인한 결과(Auto Mode는 FIS EC2 액션을 지원하지 않습니다)로, Spot 중단 경고를 받자마자 노드를 비웠습니다. 2분을 기다려 주지 않으므로, Spot에서 버티는 힘은 replica를 여러 노드에 나눠 두는 배치에서 나옵니다.</div>

<nav class="toc" markdown="1">

**목차**

1. [시험 환경](#env)
2. [EKS 1.37과 Karpenter 1.15에서 달라진 점](#versions)
3. [데모 1: 같은 Pending Pod 10개](#demo1)
4. [데모 2: 정책을 비워 둔 NodePool의 consolidation](#demo2)
5. [데모 3: NodeClass를 바꾸면 노드는 한 대씩 교체된다](#demo3)
6. [데모 4: Spot 중단 경고를 받으면](#demo4)
7. [스케줄러 점수 전략과 in-place resize](#sched)
8. [구성하며 막혔던 지점](#pitfalls)
9. [자주 받은 질문](#faq)
10. [마치며](#wrap)

</nav>

<h2 id="env">1. 시험 환경</h2>

| 구분 | akd-auto (EKS Auto Mode) | akd-karp (직접 설치 Karpenter) |
|---|---|---|
| Kubernetes | EKS 1.37 (platform eks.5), ap-northeast-2 | 같음 |
| 노드 관리 | Auto Mode built-in 기능 | Karpenter controller 1.15.0 (Helm), 관리형 노드 그룹 m7i.large 2대에서 실행 |
| 노드 OS | Bottlerocket (EKS Auto, Standard) 2026.10.9 | Amazon Linux 2023, AMI `v20261003`으로 시작 |
| 노드 정의 | NodeClass `demo` (`eks.amazonaws.com/v1`) | EC2NodeClass `default` (`karpenter.k8s.aws/v1`) |
| Spot 중단 처리 | AWS가 처리 | SQS interruption queue와 EventBridge 규칙 (CloudFormation) |

두 클러스터의 `demo` NodePool 조건은 똑같이 맞췄습니다. on-demand, c, m, r 계열, 5세대 초과, amd64이고, `consolidateAfter: 30s`, disruption budget은 `nodes: "1"`입니다. `consolidationPolicy`는 두 쪽 모두 일부러 비워 두었습니다. 워크로드는 pause 컨테이너이고, replica 하나가 1 vCPU와 1.5Gi를 요청합니다. Karpenter 클러스터에는 Spot 중단 시험용 `spot` NodePool(spot 전용, large와 xlarge, 같은 EC2NodeClass)을 하나 더 두었습니다.

네트워크는 데모 전용 VPC를 그대로 썼습니다. 기존 private 서브넷 두 개에 남은 IP가 80개와 146개뿐이라, 같은 VPC 안에 데모 전용 private 서브넷 두 개(`10.0.32.0/20`, `10.0.48.0/20`)를 추가하고 기존 NAT gateway 라우팅 테이블에 연결했습니다. 새 VPC는 만들지 않았고, 서브넷은 시험이 끝난 뒤 지웠습니다. 노드에는 공인 IP를 할당하지 않았고, EKS API의 public endpoint는 작업 PC의 출구 IP 하나(`/32`)만 허용했습니다.

<h2 id="versions">2. EKS 1.37과 Karpenter 1.15에서 달라진 점</h2>

EKS 1.37은 2026년 10월 1일에 나왔습니다. 서울 리전의 기본 버전은 아직 1.36이라 클러스터를 만들 때 `version: "1.37"`을 명시해야 했습니다.

직접 설치하는 Karpenter는 버전을 맞춰야 합니다. Karpenter 호환성 표에서 Kubernetes 1.37에 맞는 버전은 1.15.x뿐입니다. v1.15.0은 2026년 10월 10일에 나온 regular release이고 LTS가 아닙니다. LTS인 v1.14.x는 2027년 7월까지 지원되지만 Kubernetes 1.36까지만 해당합니다(v1.14.2 패치는 10월 19일에 나왔습니다). 그래서 직접 운영하는 클러스터를 1.37로 올리려면 Karpenter와 CRD를 먼저 1.15.x로 올리고, 그다음에 control plane을 올려야 합니다. Auto Mode에는 사용자가 고르는 Karpenter 버전이 없고 AWS가 관리합니다.

consolidation 정책에도 변화가 있었습니다. `Balanced`는 upstream Karpenter v1.14.0(2026년 7월 11일)에 opt-in으로 들어왔고, Auto Mode에는 2026년 7월 27일에 추가되었습니다. 그리고 EKS 1.37부터 Auto Mode에서 **새로 만드는** NodePool 중 정책을 지정하지 않은 것은 `Balanced`가 기본값이 됩니다. built-in NodePool인 `general-purpose`와 `system`도 1.37에서 `Balanced`로 바뀌고, EKS가 매시간 다시 맞추므로 덮어쓸 수 없습니다.

| consolidationPolicy | 동작 |
|---|---|
| `WhenEmpty` | Pod가 없는 노드만 회수 |
| `WhenEmptyOrUnderutilized` | 덜 쓰이는 노드도 교체 대상. 작은 절감에도 교체. upstream 기본값 |
| `Balanced` | 절감 비율이 그 동작으로 영향받는 Pod 비율만큼 가치 있을 때만 실행 |

기본값은 객체를 만들 때만 들어간다는 점이 중요합니다. 업그레이드만으로 기존 NodePool이 바뀌지는 않지만, GitOps 도구가 정책이 빠진 NodePool을 지웠다 다시 만들거나 새 1.37 클러스터를 부트스트랩하면 `Balanced`가 들어갑니다. Git에는 값이 없는데 live 객체에는 `Balanced`가 보이는 상태가 되므로, 정책은 명시해 두는 편이 안전합니다.

<h2 id="demo1">3. 데모 1: 같은 Pending Pod 10개</h2>

<figure class="video"><video src="https://presentation.atomai.click/eks-automode-karpenter/media/v1-scaleout.mp4" poster="https://presentation.atomai.click/eks-automode-karpenter/media/v1-scaleout.jpg" controls preload="metadata" muted playsinline></video><figcaption>데모 1 녹화 영상 (2분 30초, 무음, 실제 터미널 녹화 포함)</figcaption></figure>

두 클러스터에 같은 순간 `scale --replicas=10`을 보냈습니다(0개에서 10개). 시각은 UTC입니다.

| 클러스터 | 선택한 노드 | NodeClaim 생성 | 노드 Ready | Pod 10개 Ready (scale 명령 기준) |
|---|---|---|---|---|
| akd-auto | c7i-flex.4xlarge 1대, on-demand | 13:54:15 | 13:54:30 (+15초) | +28초 |
| akd-karp | c7i-flex.4xlarge 1대, on-demand | 13:54:15 | 13:54:55 (+40초) | +47초 |

두 클러스터는 같은 초에 NodeClaim을 만들고 같은 인스턴스를 한 대 골랐습니다. 노드를 고르는 판단이 같은 Karpenter 로직이라는 점이 그대로 보입니다. 차이는 노드가 Ready가 되기까지의 시간이었습니다. 다만 이 값은 한 번 잰 결과이고, 노드 OS(Bottlerocket과 AL2023)와 부팅 구성이 다릅니다. 직접 운영한다면 AMI와 부팅 스크립트를 가볍게 하는 만큼 줄일 수 있는 영역이므로, "Auto Mode가 항상 빠르다"로 일반화하지는 않았습니다.

<h2 id="demo2">4. 데모 2: 정책을 비워 둔 NodePool의 consolidation</h2>

<figure class="video"><video src="https://presentation.atomai.click/eks-automode-karpenter/media/v2-consolidation.mp4" poster="https://presentation.atomai.click/eks-automode-karpenter/media/v2-consolidation.jpg" controls preload="metadata" muted playsinline></video><figcaption>데모 2 녹화 영상 (2분 50초, 무음, 실제 터미널 녹화 포함)</figcaption></figure>

먼저 정책을 쓰지 않은 두 NodePool에 실제로 어떤 값이 들어갔는지 확인했습니다.

| | akd-auto | akd-karp |
|---|---|---|
| `consolidationPolicy` (live) | `Balanced` | `WhenEmptyOrUnderutilized` |
| `expireAfter` (live) | `336h` | `720h` |

그다음 두 클러스터 모두 c7i-flex.4xlarge 한 대에 Pod 10개가 있는 상태에서 4개로 줄였습니다. 녹화한 회차(UTC 14:05)에서는 두 클러스터가 같은 동작을 했습니다. c7i-flex.2xlarge를 새로 띄우고, 새 노드가 Ready가 된 뒤 기존 4xlarge를 비우면서 Pod 4개를 옮겼습니다. 이벤트 순서도 같았습니다(`DisruptionLaunching`, 새 노드 Ready, `DisruptionTerminating`, `Evicted` 4건). 처음 이동한 Pod는 Auto Mode에서 scale-in 후 67초, Karpenter에서 75초에 관찰했습니다.

Auto Mode 쪽은 승인 이벤트에 점수를 남겼습니다.

```text
ConsolidationCandidate  replace: [i-...] -> [1 replacement] (savings: $0.38)
ConsolidationApproved   score 0.50 >= threshold 0.50 (k: 2, savings 50.0%, disruption 100.0%)
```

노드가 한 대뿐이라 영향받는 Pod 비율이 100%였고, 절감이 50%라 점수 0.5가 임계값 0.5와 같아 승인되었습니다. 점수는 옮기는 Pod 비율 대비 절감 비율로 정해지므로, NodePool 크기가 바뀌어도 이 비율은 그대로입니다.

시간 차이는 비교하지 않았습니다. 녹화 전에 먼저 돌린 회차(UTC 13:55, 녹화가 중간에 끊겨 영상에는 쓰지 않음)에서도 결과는 같았지만(4xlarge를 2xlarge로 교체, Pod 4개 이동), 그때는 Karpenter가 scale-in 후 약 1분, Auto Mode가 약 5분 30초 뒤에 움직였습니다. 회차마다 시점이 달라서 이것을 정책 간 속도 차이로 보기는 어렵습니다.

<h2 id="demo3">5. 데모 3: NodeClass를 바꾸면 노드는 한 대씩 교체된다</h2>

<figure class="video"><video src="https://presentation.atomai.click/eks-automode-karpenter/media/v3-drift.mp4" poster="https://presentation.atomai.click/eks-automode-karpenter/media/v3-drift.jpg" controls preload="metadata" muted playsinline></video><figcaption>데모 3 녹화 영상 (2분 40초, 무음, 실제 터미널 녹화 포함)</figcaption></figure>

각 클러스터에 demo 노드 두 대(2a, 2b)와 Pod 6개를 두고 NodeClass를 바꿨습니다. Karpenter 쪽은 EC2NodeClass의 AMI alias를 `al2023@v20261003`에서 `al2023@v20261012`로 올렸습니다. Auto Mode는 AMI를 사용자가 고르지 않으므로, NodeClass에 `advancedCompute.kubelet.maxPods: 64`를 추가했습니다.

```bash
kubectl --context akd-karp patch ec2nodeclass default --type merge \
  -p '{"spec":{"amiSelectorTerms":[{"alias":"al2023@v20261012"}]}}'
kubectl --context akd-auto patch nodeclass demo --type merge \
  -p '{"spec":{"advancedCompute":{"kubelet":{"maxPods":64}}}}'
```

패치 직후 두 클러스터 모두 기존 NodeClaim 두 개가 `Drifted=True`가 되었고, budget `nodes: "1"`에 따라 새 노드를 먼저 띄운 뒤 한 대씩 교체했습니다(시각은 UTC).

| 클러스터 | Launch, Terminate, Launch, Terminate | 완료 (기존 NodeClaim 0개, 6/6 Running) |
|---|---|---|
| akd-auto | 14:20:12, 14:20:30, 14:20:45, 14:21:00 | 패치 후 +98초 |
| akd-karp | 14:20:10, 14:20:46, 14:20:57, 14:21:27 | 패치 후 +127초 |

결과를 확인하며 두 가지를 배웠습니다.

- **maxPods는 상한입니다.** 64를 넣었지만 새 Auto Mode 노드의 `capacity.pods`는 56이었습니다. 실제값은 설정값, 인스턴스가 가질 수 있는 IP 수, 110 중 가장 작은 값입니다.
- **같은 EC2NodeClass를 쓰는 NodePool은 함께 교체됩니다.** AMI를 바꾸자 같은 `default` EC2NodeClass를 쓰던 spot NodePool 노드도 Drift로 교체되었습니다. NodeClass를 바꾸기 전에 어떤 NodePool이 그것을 참조하는지 먼저 확인해야 합니다.

AMI alias는 `@latest`로 두면 새 AMI가 나올 때마다 모든 노드가 한꺼번에 Drift 대상이 됩니다. 날짜 버전으로 고정해 두고, 올리는 시점을 사람이 정하는 편을 권합니다. Auto Mode는 AWS가 대략 주 1회 새 AMI를 내고 그것이 Drift 신호가 되어 노드가 교체됩니다.

<h2 id="demo4">6. 데모 4: Spot 중단 경고를 받으면</h2>

<figure class="video"><video src="https://presentation.atomai.click/eks-automode-karpenter/media/v4-spot.mp4" poster="https://presentation.atomai.click/eks-automode-karpenter/media/v4-spot.jpg" controls preload="metadata" muted playsinline></video><figcaption>데모 4 녹화 영상 (2분 30초, 무음, 실제 터미널 녹화 포함)</figcaption></figure>

이 데모는 Karpenter 클러스터에서만 돌렸습니다. Auto Mode 인스턴스는 AWS Fault Injection Service(FIS)의 EC2 액션을 지원하지 않기 때문입니다. Auto Mode에서는 Spot 중단 알림을 AWS가 처리하고, 직접 운영하는 Karpenter는 Helm `settings.interruptionQueue`로 지정한 SQS 큐에서 알림을 읽습니다. 큐로 알림을 보내는 EventBridge 규칙은 Karpenter CloudFormation 템플릿에 다섯 개(Spot 중단, rebalance, scheduled change, 인스턴스 상태 변경, capacity reservation 중단)가 있고, 이번 시험에서는 Spot 중단, rebalance, 인스턴스 상태 변경 규칙으로 메시지가 들어왔습니다(아래 표의 `InstanceTerminating`이 상태 변경 메시지입니다).

FIS 실험(`aws:ec2:send-spot-instance-interruptions`, 2분 전 알림)으로 Spot 인스턴스 한 대에 중단 알림을 보냈습니다. 대상 노드(m7i.xlarge spot)에는 spot-app Pod 3개가 모두 있었습니다. 시각은 UTC입니다.

| 시각 | 이벤트 |
|---|---|
| 14:23:15 | FIS 실험 시작 |
| 14:23:28 | `SpotRebalanceRecommendation` |
| 14:23:29 | `SpotInterrupted`, `TerminatingOnInterruption`, Pod 3개 `Evicted: Forceful Termination`, 대체 NodeClaim 지명 |
| 14:23:33 | 대체 NodeClaim `Launched` |
| 14:23:35 | 기존 인스턴스 `InstanceTerminating` |
| 14:24:00 | 대체 노드(c7i-flex.xlarge spot) Ready, Pod 다시 Running |

Karpenter는 2분을 기다리지 않았습니다. 경고를 받은 즉시 Pod를 비우고 대체 노드를 요청했으며, 6초 뒤에 기존 인스턴스를 직접 종료했습니다. Pod 3개가 그 노드 하나에 있었기 때문에, 대체 노드가 준비될 때까지 **약 30초 동안 Running Pod가 0개**였습니다.

이번 시험에는 PodDisruptionBudget(PDB)을 두지 않았습니다. Karpenter 문서에 따르면 interruption 같은 강제(forceful) 방식은 NodePool budget으로 늦출 수 없고 대체 노드가 준비되기를 기다리지도 않지만, drain은 Eviction API를 쓰므로 PDB로 애플리케이션 중단 속도는 제한할 수 있습니다. 다만 2분 뒤 EC2가 인스턴스를 회수하는 것까지 막지는 못합니다. 그래서 Spot 중단에 대비하려면 topology spread로 replica를 여러 노드와 zone에 나누고, NodePool에 여러 인스턴스 유형을 허용해 대체 용량을 찾기 쉽게 해 두어야 합니다. 처리 주체가 AWS인 Auto Mode에서도 배치 설계는 팀의 몫입니다.

<h2 id="sched">7. 스케줄러 점수 전략과 in-place resize</h2>

이번 시험에서는 측정하지 않았지만, 세션 준비 중 같이 정리한 두 기능이 consolidation과 맞닿아 있어 함께 적습니다.

**스케줄러 점수 전략.** 노드를 고르는 것은 Karpenter지만, 이미 있는 노드 중 어디에 Pod를 놓을지는 kube-scheduler가 정합니다. 2026년 8월 12일부터 EKS 고급 control plane 구성(advanced Kubernetes control plane configuration)으로 kube-scheduler의 `nodeResourcesFit` 점수 전략을 바꿀 수 있습니다. Kubernetes 1.31 이상이면 신규와 기존 클러스터 모두 쓸 수 있고, 1.36과 함께 나온 기능은 아닙니다.

```bash
aws eks update-cluster-config --name "$CLUSTER" \
  --kube-scheduler-config '{"nodeResourcesFit":{"scoringStrategy":{"type":"MostAllocated"}}}'
```

기본값 `LeastAllocated`는 여유가 많은 노드로 Pod를 흩고, `MostAllocated`는 이미 많이 쓰는 노드로 모읍니다. 적게 쓰인 노드에 새 Pod가 가지 않으니 그 노드가 비면 consolidation이 정리하기 쉬워집니다. cpu, memory, `nvidia.com/gpu`, Neuron 자원에 1에서 100까지 가중치를 줄 수도 있습니다. 다만 클러스터 전체에 적용되고, 실행 중인 Pod는 옮기지 않으며, 모을수록 노드 하나가 죽을 때 영향받는 Pod가 많아집니다. AWS 문서에 명시된 대로 이 설정은 Auto Mode나 Karpenter가 노드를 만들고 지우는 방식은 바꾸지 않습니다. 이번 데모에서는 기본값을 그대로 두었습니다.

**In-place Pod resize.** Pod를 다시 만들지 않고 CPU와 memory를 바꾸는 기능은 1.35에서 GA가 되었습니다. 1.36에서는 Pod 단위 리소스(`.spec.resources`)의 in-place resize가 Beta로 기본 켜졌고, 1.37에서는 노드가 꽉 차 `Deferred`로 멈춘 resize를 위해 scheduler가 낮은 priority Pod를 preempt하는 기능이 Alpha로 들어왔습니다.

이 기능은 2026년 6월에 EKS 1.36.1(AL2023 arm64 노드, Auto Mode 아님)에서 따로 측정한 적이 있습니다. startup probe가 통과하면 작은 컨트롤러가 `pods/resize`로 CPU를 줄이게 했고, resizePolicy는 1.36에서 GA된 MutatingAdmissionPolicy로 webhook 없이 넣었습니다.

| 워크로드 | CPU (req = limit) | QoS | restartCount | containerID |
|---|---|---|---|---|
| Deployment (Pod 2개) | 200m → 50m | Guaranteed 유지 | 0 → 0 | 동일 |
| StatefulSet | 200m → 50m | Guaranteed 유지 | 0 → 0 | 동일 |
| DaemonSet | 200m → 50m | Guaranteed 유지 | 0 → 0 | 동일 |

노드 자동화와 엮이는 지점은 Karpenter입니다. Karpenter는 v1.14.0부터 Pod 크기를 `spec.requests`와 `status.allocatedResources` 중 큰 값으로 계산해, resize 중에도 kube-scheduler와 같은 판단을 합니다. requests가 줄어 노드에 여유가 생기면 consolidation 대상이 될 수 있습니다. QoS class는 생성 시 정해져 바뀌지 않고, memory 축소는 컨테이너 재시작을 동반할 수 있습니다. Auto Mode에서의 resize 관련 동작은 AWS 문서에 따로 설명이 없어 직접 확인해야 합니다. 측정 과정과 코드는 [EKS 1.36 What's New 정리](https://atomoh.notion.site/Amazon-EKS-1-36-What-s-New-2026-06-3748907d4b018102983bd4a31f243512)의 하위 페이지에 있습니다.

<h2 id="pitfalls">8. 구성하며 막혔던 지점</h2>

**Auto Mode 커스텀 NodeClass에 tags를 넣으면 Ready가 되지 않았습니다.** NodeClass에 자체 태그(`tags`)를 넣었더니 `ValidationSucceeded=False`, 메시지는 `Controller isn't authorized to call ec2:CreateLaunchTemplate`였습니다. 태그를 빼자 바로 Ready가 되었습니다. AWS 문서에 따르면 Auto Mode 관리형 정책은 기본적으로 사용자 정의 태그를 허용하지 않으므로, 커스텀 태그가 필요하면 클러스터 IAM role에 태그 생성과 수정 권한을 더해야 합니다([Custom AWS tags for EKS Auto resources](https://docs.aws.amazon.com/eks/latest/userguide/auto-cluster-iam-role.html)). 이번에는 태그 없이 진행했고, 노드에는 `eks:eks-cluster-name` 태그가 자동으로 붙습니다.

**NodePool에 `disruption`을 쓰면 `consolidateAfter`도 써야 했습니다.** budget만 넣고 정책과 `consolidateAfter`를 비워 두었더니 Auto Mode는 `spec.disruption.consolidateAfter: Required value`로 NodePool을 거부했습니다. 두 클러스터 모두 built-in pool과 같은 `30s`를 넣고, 정책만 비워 두었습니다.

**zone topology spread만으로는 노드가 두 대가 되지 않았습니다.** `topologySpreadConstraints`(zone, `maxSkew: 1`, `DoNotSchedule`)를 넣어도 Pod 6개가 2b 노드 한 대에 모두 들어갔습니다. 스케줄러는 지금 있는 노드의 zone만 domain으로 세기 때문입니다. `minDomains: 2`를 더하자 Karpenter가 2a에 노드를 하나 더 만들었습니다.

**서브넷 IP가 부족했습니다.** 두 클러스터와 Auto Mode의 prefix delegation(`/28`)을 감당하기에 기존 `/24` 서브넷 두 개의 남은 IP가 부족했습니다. 같은 VPC 안에 `/20` 서브넷을 추가해 해결했습니다.

**Karpenter 설치에서 확인한 것들입니다.**

- Helm chart 버전 문자열은 `1.15.0`입니다. `--version v1.15.0`은 not found가 납니다.
- v1.15.0 태그의 `docs/` 경로 CloudFormation 템플릿은 1.14 내용이라 `ec2:RebootInstances` 권한이 없습니다. Karpenter 설치 스크립트가 받는 `preview/` 경로 템플릿을 썼습니다. 그리고 SQS 큐 정책에서 `sqs.amazonaws.com` principal을 빼고 `aws:SourceAccount` 조건을 더해, 같은 계정의 EventBridge만 메시지를 보내도록 좁혔습니다.
- eksctl은 v0.231.0을 썼습니다. Karpenter 문서는 v0.230.0 이상을 요구합니다.

**FIS 대상 지정.** FIS 템플릿은 태그 `karpenter.sh/nodepool=spot`과 `eks:eks-cluster-name=akd-karp`로 대상을 골랐습니다. Karpenter가 만든 인스턴스에는 두 태그가 모두 붙어 있었습니다. FIS role에는 `ec2:SendSpotInstanceInterruptions`를 같은 두 태그 조건으로만 허용했습니다.

**Auto Mode 인스턴스는 EC2 목록에서 기본으로 숨겨집니다.** 2026년 4월 22일 이후 만들어진 Auto Mode 관리 인스턴스는 EC2 콘솔과 describe 목록에 기본으로 보이지 않습니다. 노드 확인은 `kubectl get nodeclaims`로 했습니다.

<h2 id="faq">9. 자주 받은 질문</h2>

### capacity-type을 비워 두면 Auto Mode와 직접 설치한 Karpenter의 동작이 다른가요?

같습니다. 두 방식 모두 reserved, spot, on-demand 순서로 고릅니다. 직접 설치한 Karpenter 1.15.0 코드에서도 확인했습니다. `karpenter-provider-aws`의 `getCapacityType`이 reserved, spot 순으로 띄울 수 있는 offering을 찾고 없으면 on-demand로 내려가며, 지정하지 않은 requirement는 core에서 "모든 값 허용"으로 처리합니다. 차이는 built-in pool에 있습니다. Auto Mode의 `general-purpose`, `system`은 On-Demand로 고정이라 Spot을 쓰려면 커스텀 NodePool이 필요합니다. 반대로 직접 운영하는 NodePool에서 capacity-type을 비워 두면 Spot이 먼저 선택되니, On-Demand만 써야 하는 워크로드는 명시해야 합니다. reserved는 사용할 capacity reservation이 있을 때만 대상이 됩니다.

### Balanced를 직접 설치한 Karpenter에서도 쓸 수 있나요?

쓸 수 있습니다. upstream v1.14.0부터 `consolidationPolicy: Balanced`를 지정할 수 있고, 기본값은 여전히 `WhenEmptyOrUnderutilized`입니다. 1.37에서 기본값이 바뀐 것은 Auto Mode의 새 NodePool입니다.

### Auto Mode에서 Karpenter 버전은 어떻게 확인하나요?

사용자가 고르거나 확인하는 버전이 없습니다. AWS가 관리형 구성 요소를 업데이트하고, 노드는 새 AMI가 나오면 Drift로 교체됩니다. 직접 설치한 경우에는 위처럼 Karpenter를 먼저 올린 뒤 Kubernetes를 올리는 순서를 지켜야 합니다.

<h2 id="wrap">10. 마치며</h2>

Auto Mode와 직접 설치한 Karpenter는 같은 판단을 하는 같은 엔진이었고, 선택의 기준은 무엇을 직접 운영할지였습니다. custom AMI, userData, 세밀한 kubelet 설정, feature gate, FIS 장애 주입이 필요하면 직접 운영이 맞고, Karpenter minor 버전과 CRD 변경을 따라갈 인력이 부족하거나 애드온 운영을 줄이고 싶다면 Auto Mode가 부담을 덜어 줍니다. 어느 쪽이든 정책을 명시하고, budget과 PDB로 교체 속도를 정하고, replica를 나눠 두는 일은 팀에 남습니다. 직접 운영한다면 AMI 버전 고정과 올리는 시점도 팀이 정합니다.

| 확인한 것 | 남은 것 |
|---|---|
| 같은 조건에서 두 클러스터가 같은 인스턴스를 같은 대수로 선택 | 노드 Ready 시간의 반복 측정 (OS를 맞춘 비교 포함) |
| 정책 미지정 시 Auto Mode `Balanced`, Karpenter `WhenEmptyOrUnderutilized` | 노드가 여러 대일 때 `Balanced`가 교체를 보류하는 경우 |
| Balanced 승인 이벤트의 점수(0.50 대 0.50) | consolidation 시점이 회차마다 달랐던 원인 |
| Drift 교체가 budget 1대로 순차 진행 | `MostAllocated`와 consolidation을 함께 쓸 때의 효과 |
| Spot 중단 직후 즉시 drain, 한 노드에 몰린 replica는 약 30초 공백 | Auto Mode에서의 in-place resize 동작 |

환경을 켜 둔 동안에는 EKS control plane 두 개, 관리형 노드 그룹, Auto Mode 관리 비용, 데모 노드의 EC2 비용이 발생합니다. Auto Mode 관리 비용은 인스턴스 유형별 단가로 공시되고, Spot이나 Savings Plans 할인으로 줄지 않습니다. 녹화가 끝난 직후 정리 스크립트로 demo NodePool, FIS 템플릿과 role, Karpenter 스택, 두 클러스터, 데모 전용 서브넷까지 순서대로 지웠고, 남은 리소스가 없는지 확인했습니다.

<p class="refs"><strong>참고 자료</strong></p>

- [Amazon EKS Auto Mode](https://docs.aws.amazon.com/eks/latest/userguide/automode.html)
- [Create a Node Class for Amazon EKS](https://docs.aws.amazon.com/eks/latest/userguide/create-node-class.html)
- [Create a Node Pool for EKS Auto Mode](https://docs.aws.amazon.com/eks/latest/userguide/create-node-pool.html)
- [Amazon EKS Kubernetes versions](https://docs.aws.amazon.com/eks/latest/userguide/kubernetes-versions.html)
- [Advanced Kubernetes control plane configuration](https://docs.aws.amazon.com/eks/latest/userguide/control-plane-configuration.html)
- [Karpenter: Disruption (interruption과 PDB 포함)](https://karpenter.sh/docs/concepts/disruption/), [Compatibility](https://karpenter.sh/docs/upgrading/compatibility/), [v1.15.0 릴리스](https://github.com/aws/karpenter-provider-aws/releases/tag/v1.15.0)
- [Kubernetes v1.36: In-Place Vertical Scaling for Pod-Level Resources Graduates to Beta](https://kubernetes.io/blog/2026/04/30/kubernetes-v1-36-inplace-pod-level-resources-beta/)
- [Kubernetes v1.37: Scheduler Preemption for In-Place Pod Resize (Alpha)](https://kubernetes.io/blog/2026/09/10/kubernetes-v1-37-scheduler-preemption-for-in-place-pod-resize-alpha/)
- 세션 자료: [EKS Auto Mode와 Karpenter (65분) 덱](https://presentation.atomai.click/eks-automode-karpenter/)
