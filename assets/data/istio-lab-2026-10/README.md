# Istio sidecar vs ambient 측정 원시 결과 (2026-10-20 ~ 21)

글: https://blog.atomai.click/istio-on-eks/

환경: EKS 1.36.4, Istio 1.31.2 (ambient profile, sidecar 주입 병행), m7g.xlarge 3대(client 1, server 2, 모두 ap-northeast-2a), fortio 1.76.0.
네임스페이스 4개(base, sidecar, ambient-l4, ambient-l7)에 같은 echo(3 replica)와 client를 두고 데이터 플레인만 바꿨습니다.

| 파일 | 내용 |
|---|---|
| latency-20261021-0121.json | 200, 1000 req/s, 연결 16개, 60초, 3회. 히스토그램 해상도 0.1ms. 프록시 CPU(kubelet stats) |
| rollout-20261021-0051.json | 부하 중 `rollout restart` 3회, 기본 spec과 preStop sleep 10s. 재시도 카운터 포함 |
| rollout-20261020-1413.json | 같은 시험의 첫 회차(재시도 카운터 없음) |
| policy-20261020-1340.json | L7 조건(`paths /admin*`) AuthorizationPolicy, 2초 간격 60초 관찰 |
| policy-20261020-1327-first-attempt-10s-wait.json | 첫 시도. 적용 후 10초만 기다려 sidecar 결과가 아직 반영 전이었음 |
| mtls-20261020-1342.json | PERMISSIVE, STRICT에서 메시 밖 Pod의 평문 호출 |
| obs-20261020-1325.json | 같은 요청 조합에 대해 모드별로 남는 메트릭과 로그 |
| outlier-20261020-1351.json | 정상 3개 + 항상 500인 엔드포인트 1개, 기본값과 outlierDetection |
| outlier-conn-20261021-0028.json | 같은 조건에서 keep-alive 켬/끔 (L4 경로의 연결 단위 분산) |

`lab/`은 재현용 스크립트입니다. VPC, 서브넷, 계정, 접속 IP는 자리표시자로 바꿨습니다.

설치·설정·삭제 스크립트를 실행하기 전에 `EXPECTED_ACCOUNT`를 사용할 AWS 계정 ID로 직접 지정하고 export하세요. 삭제 스크립트는 계정이 일치할 때만 실행되며, 클러스터 생성 중 실패한 경우에도 정리할 수 있도록 kubeconfig는 요구하지 않습니다.

삭제 보호 동작은 저장소 루트에서 `python3 -m unittest discover -s tests -v`로 확인할 수 있습니다. 테스트는 AWS·Kubernetes CLI를 가짜 명령으로 대체해 실제 클라우드에 접근하지 않습니다.
