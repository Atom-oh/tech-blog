---
workflow: general-video
flow: automation
storyboard: no
message: "Aurora MySQL의 자동 Failover와 전환 후 쓰기 복구"
destination: blog-embed
aspect: "16:9"
language: ko
audience: AWS operators and backend developers
length: 50s
---

## Intent
사용자 요청: Aurora MySQL 수동 Failover 동작을 데모 영상과 함께 설명하고 자동 Failover, 사용 시점, Multi-AZ 가용성을 비교하는 한국어 블로그.

## Production decisions
설명용 시뮬레이션, 1280×720, 24fps, 무음, 한국어 화면 설명과 WebVTT 자막.
실제 AWS 녹화·실측이 아니라는 표시를 모든 장면에 유지한다.
수동·자동 영상은 같은 인스턴스 예시와 레이아웃을 사용한다.
AWS 클러스터를 생성하거나 장애 작업을 실행하지 않는다.
CLI와 설치된 core/general-video 스킬은 0.8.181 환경을 사용하며 전역 스킬 변경은 생략한다.

## Storyboard
0–8초: 정상 Writer·Reader 구성.
8–18초: 장애 감지.
18–28초: Reader 승격 경로.
28–38초: Endpoint를 통한 새 연결.
38–50초: 역할 변경과 실제 쓰기 확인.

## Assets
GSAP 3.14.2 로컬 파일, Noto Sans CJK KR의 글리프 subset.
직접 작성한 역할·Endpoint 도식.

## Motion
짧은 opacity·y 진입과 SVG path draw. 단일 paused timeline, seek-safe.
모든 화면 상태·시간은 설명용이며 서비스 이벤트 기록을 모사한 실측 결과가 아니다.
