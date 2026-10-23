---
title: "Aurora MySQL Failover: 수동 전환 데모와 자동 복구, Multi-AZ가 지켜 주는 범위"
date: 2026-10-23
description: "Aurora MySQL의 수동·자동 Failover를 동작 데모 영상으로 비교합니다. 스토리지 복제와 인스턴스의 Multi-AZ 배치를 구분하고, Reader 승격 우선순위, Writer Endpoint, 재접속과 쓰기 복구를 확인하는 실습 절차를 정리했습니다."
tags: aurora, mysql, rds, failover, multi-az
---

# Aurora MySQL Failover: 수동 전환 데모와 자동 복구, Multi-AZ가 지켜 주는 범위

<p class="lead">Aurora MySQL은 Writer에 문제가 생기면 자동으로 복구하고, 필요할 때 운영자가 직접 Reader를 Writer로 승격할 수도 있습니다. 하지만 “Aurora니까 Multi-AZ다”라는 말만으로 서비스 가용성을 판단하면 중요한 부분을 놓칩니다. 데이터가 여러 AZ에 저장되는 것과, 다른 AZ에서 SQL을 처리할 인스턴스가 준비되어 있는 것은 별개입니다. 이 글에서는 두 가지 Failover의 동작을 데모 영상으로 비교하고, 실제 운영에서 전환과 쓰기 복구를 어떻게 확인할지 정리합니다.</p>

<div class="callout"><strong>글과 영상의 범위</strong>2026년 10월 23일 확인한 AWS 공식 문서를 바탕으로 작성했습니다. 대상은 한 리전 안의 일반 Aurora MySQL DB 클러스터입니다. 아래 두 영상은 동작을 설명하는 <b>시뮬레이션</b>이며 실제 AWS 콘솔·터미널 녹화나 장애 실측이 아닙니다. 영상의 재생 시간은 Failover 소요 시간이 아닙니다. 실제 환경에서 재현할 CLI와 쓰기 관측 스크립트를 함께 제공합니다.</div>

<div class="callout warn"><strong>먼저 알아둘 다섯 가지</strong>
① Aurora MySQL은 자동 Failover를 지원합니다. 다만 모든 장애에서 반드시 다른 인스턴스로 전환하는 것은 아니며, 같은 인스턴스를 재시작하는 것이 빠르면 그 경로로 복구할 수 있습니다.<br>
② AZ 장애에 대비하려면 Writer와 적어도 한 Reader를 서로 다른 AZ에 둡니다.<br>
③ 수동 Failover도 기존 연결과 진행 중인 작업에 영향을 줍니다. 계획된 전환이라고 무중단은 아닙니다.<br>
④ Writer Endpoint 이름은 유지되지만 대상 인스턴스와 DNS 응답은 바뀝니다. 기존 TCP 연결은 새 Writer로 옮겨 가지 않습니다.<br>
⑤ 완료 여부는 Writer 역할 변경에 더해 애플리케이션의 <b>쓰기 성공</b>으로 확인합니다. <a href="#ref-ha">[1]</a> <a href="#ref-endpoints">[2]</a> <a href="#ref-api">[3]</a>
</div>

<nav class="toc" markdown="1">

**목차**

1. [Multi-AZ: 스토리지와 컴퓨트를 나누어 보기](#availability)
2. [수동과 자동 Failover는 무엇이 다른가](#comparison)
3. [데모 1: Reader를 지정하는 수동 Failover](#manual)
4. [실습: Writer 변경과 쓰기 복구 확인하기](#lab)
5. [데모 2: 장애 감지 후 자동 Failover](#automatic)
6. [자동 Failover 시험에서 헷갈리기 쉬운 명령](#fault-injection)
7. [전환은 끝났는데 애플리케이션이 복구되지 않을 때](#application)
8. [언제 수동 전환을 쓰고, 어떤 구성을 선택할까](#operations)
9. [복구 시간과 데이터 확인, 참고 자료](#references)

</nav>

<h2 id="availability">1. Multi-AZ: 스토리지와 컴퓨트를 나누어 보기</h2>

Aurora의 스토리지는 인스턴스와 분리되어 있습니다. 클러스터 데이터는 **3개 AZ의 6개 스토리지 복사본**에 걸쳐 보관되고, Writer의 쓰기는 AZ에 걸쳐 동기적으로 복제됩니다. Writer가 한 AZ에 한 대만 있어도 이 스토리지 보호는 적용됩니다. Reader마다 독립적인 전체 데이터 볼륨을 만들어 복사하는 구조로 이해하면 안 됩니다. <a href="#ref-ha">[1]</a> <a href="#ref-overview">[4]</a>

[![한 리전의 AZ A에 Writer, AZ B에 승격 후보 Reader, AZ C에 추가 Reader를 둔 Aurora MySQL 구성. 모든 인스턴스는 세 AZ에 여섯 복사본을 둔 공유 스토리지 볼륨을 사용하며, 애플리케이션은 Writer Endpoint로 접속한다.](/assets/aurora-failover-architecture.svg)](/assets/aurora-failover-architecture.svg)

[구성도 원본 크게 보기](/assets/aurora-failover-architecture.svg)

하지만 스토리지가 살아 있다는 사실만으로 SQL 요청을 받을 수 있는 것은 아닙니다. Writer의 AZ 전체가 사용할 수 없으면 다른 AZ에서 Writer 역할을 맡을 컴퓨트가 필요합니다. AWS는 Writer와 Reader를 여러 AZ에 분산하라고 권장합니다. <a href="#ref-ha">[1]</a>

| 인스턴스 구성 | 인스턴스 장애·AZ 장애 때의 의미 | 운영 판단 |
|---|---|---|
| Writer 1대 | 스토리지는 여러 AZ에 복제되지만 기존 Reader 승격 경로가 없음. 일반 인스턴스 장애는 같은 AZ의 Writer 재생성으로 복구하며, AZ 전체 장애에는 다른 AZ에 새 인스턴스를 수동 생성해야 할 수 있음 | 쓰기 중단 허용 범위를 먼저 확인 |
| Writer + Reader, 같은 AZ | Reader는 승격 후보가 될 수 있지만 두 인스턴스가 같은 AZ 장애에 함께 영향받음 | AZ 장애 대비 구성으로는 부족 |
| Writer + Reader, 다른 AZ | Writer 장애 때 준비된 다른 AZ의 Reader를 승격할 수 있음 | AZ 장애에 대비하는 기본 구성으로 권장 |
| Writer + Reader 2대, 3개 AZ | 서로 다른 AZ의 후보가 있고, 승격 대상 외의 정상 Reader에서 읽기를 계속 처리할 여지가 있음 | 읽기 연속성과 후보 여유가 필요한 경우 검토 |

이 표의 복구 동작은 AWS의 Aurora 가용성 문서를 기준으로 정리한 것입니다. 마지막 열은 이 글의 구성 권고이며 서비스의 SLA 수치를 뜻하지 않습니다. <a href="#ref-ha">[1]</a>

### RDS MySQL의 “Multi-AZ”와도 구분합니다

| 배포 방식 | 대기·Reader의 역할 | Failover 관점 |
|---|---|---|
| RDS MySQL Multi-AZ DB **instance** | 다른 AZ의 standby는 일반적으로 읽기 트래픽을 처리하지 않음 | 장애 때 standby로 전환 |
| RDS MySQL Multi-AZ DB **cluster** | 3개 AZ에 Writer 1대와 읽기 가능한 standby 2대 | 읽기 가능한 standby를 승격 |
| Aurora MySQL DB cluster | 공유 분산 스토리지와 Writer·Aurora Reader. Reader는 최대 15대 | 기존 Reader 승격 또는 Writer 재생성 |

이들은 서로 다른 배포 방식입니다. 같은 “Multi-AZ”라는 이름을 보고 standby의 읽기 가능 여부나 대상 지정 옵션을 그대로 적용하면 안 됩니다. <a href="#ref-rds-instance">[5]</a> <a href="#ref-rds-cluster">[6]</a> <a href="#ref-ha">[1]</a>

<h2 id="comparison">2. 수동과 자동 Failover는 무엇이 다른가</h2>

두 방식 모두 Writer 역할을 다른 인스턴스가 이어받는 전환을 다룹니다. **수동은 운영자가 전환을 요청하고, 자동은 Aurora가 장애를 감지해 복구 경로를 선택합니다.** 수동으로 전환했어도 이후의 자동 장애 복구 기능은 계속 적용됩니다. <a href="#ref-api">[3]</a> <a href="#ref-ha">[1]</a>

| 비교 항목 | 수동 Failover | 자동 Failover |
|---|---|---|
| 시작 조건 | 콘솔, CLI 또는 API로 전환 요청 | Aurora가 Writer의 문제를 감지 |
| 주된 목적 | 전환 연습, 재접속 검증, 계획된 Writer 역할 이동 | 예기치 않은 장애에서 가용성 복구 |
| 승격 대상 | Aurora Reader를 명시적으로 지정 가능. 생략하면 서비스가 선택 | 사용 가능한 후보와 promotion tier 등을 고려 |
| 실행 시점 | 운영자가 작업 시간과 영향 범위를 조정 | 장애 발생과 복구 판단에 따라 결정 |
| 연결·작업 영향 | 기존 연결과 진행 중 작업에 영향 가능 | 기존 연결과 진행 중 작업에 영향 가능 |
| 성공 확인 | 지정한 Reader가 Writer인지 확인하고 실제 쓰기 검증 | 새 Writer와 장애 이벤트를 확인하고 실제 쓰기 검증 |
| 시험으로 알 수 있는 것 | 승격·재접속·재시도 경로가 동작하는지 | 장애 감지부터 애플리케이션 복구까지 동작하는지 |

비교표의 동작 근거는 Aurora 가용성 및 FailoverDBCluster 문서입니다. **수동 전환 실습을 통과했다고 자연 장애의 감지 시간이나 AZ 전체 장애 대응까지 검증한 것은 아닙니다.** 마지막 구분은 시험 범위에 대한 이 글의 판단입니다. <a href="#ref-ha">[1]</a> <a href="#ref-api">[3]</a>

### 자동 승격 순서: 숫자가 작을수록 우선입니다

promotion tier는 **0이 가장 높고 15가 가장 낮습니다.** 같은 tier에서는 더 큰 인스턴스를 우선하며, tier와 크기가 모두 같으면 특정 인스턴스가 반드시 선택된다고 보장할 수 없습니다. tier 변경 자체는 Failover를 일으키지 않습니다. 다섯 번의 Failover 시도가 실패한 뒤에는 tier가 더 이상 고려되지 않을 수 있다는 예외도 있습니다. <a href="#ref-ha">[1]</a>

예를 들어 이 글의 실습은 AZ A의 Writer, AZ B의 Reader(`tier 0`), AZ C의 추가 Reader(`tier 1`)를 가정합니다. AZ B의 Reader를 우선 후보로 삼되, AZ B도 장애 상태라면 그 후보만을 고집하는 구성으로 해석하지 않습니다. Reader의 사양은 승격 후 쓰기 부하를 감당할 수 있게 정하는 것이 이 글의 권고입니다.

<h2 id="manual">3. 데모 1: Reader를 지정하는 수동 Failover</h2>

다른 AZ의 Reader를 지정해서 Writer를 옮기는 과정을 보여 줍니다. 영상의 인스턴스 이름과 화면 상태는 설명용 예시입니다.

<figure class="video aurora-video">
<video controls preload="metadata" muted playsinline poster="/assets/videos/aurora-mysql-failover/manual-poster.jpg" aria-label="Aurora MySQL 수동 Failover 동작 시뮬레이션">
<source src="/assets/videos/aurora-mysql-failover/manual-failover.mp4" type="video/mp4">
<source src="/assets/videos/aurora-mysql-failover/manual-failover.webm" type="video/webm">
<track kind="captions" src="/assets/videos/aurora-mysql-failover/manual.ko.vtt" srclang="ko" label="한국어 단계 요약" default>
브라우저에서 영상을 재생할 수 없다면 <a href="/assets/videos/aurora-mysql-failover/manual-failover.mp4">MP4 파일</a>을 내려받으세요.
</video>
<figcaption>수동 Failover 동작 데모 · 50초 · 무음 · 설명용 시뮬레이션. 영상 길이는 장애 복구 시간과 무관합니다.</figcaption>
</figure>

영상에서 확인할 순서는 다음과 같습니다.

1. 애플리케이션이 Writer Endpoint를 통해 AZ A의 `demo-a`에 씁니다.
2. 운영자가 AZ B의 Reader `demo-b`를 대상으로 전환을 요청합니다.
3. `demo-b`가 Writer로 승격되는 동안 연결 오류나 쓰기 중단을 처리합니다.
4. Writer Endpoint를 다시 해석하고 새 Writer에 재접속합니다.
5. 역할 변경과 `INSERT` 성공을 확인합니다. 기존 Writer가 정상 복귀하면 Reader 역할을 맡을 수 있습니다. <a href="#ref-api">[3]</a> <a href="#ref-endpoints">[2]</a> <a href="#ref-failover-manage">[7]</a>

수동 Failover는 공유 클러스터의 Writer 역할을 바꾸는 작업입니다. 별도 Reader 클러스터를 독립 Writer로 분리하는 `promote-read-replica-db-cluster`와 혼동하지 않습니다. <a href="#ref-promote">[8]</a>

<h2 id="lab">4. 실습: Writer 변경과 쓰기 복구 확인하기</h2>

아래 절차는 **전용 실습 클러스터**에서 실행하는 예시입니다. 명령은 실제 Writer 전환을 요청합니다. 이 글을 작성하는 과정에서는 실행하지 않았습니다. 실습 준비 조건은 다음과 같습니다.

- 서로 다른 AZ에 Writer와 정상 상태의 Aurora Reader가 있습니다.
- AWS CLI가 올바른 계정·리전을 사용하고 있으며 클러스터 조회, 이벤트 조회, Failover 권한이 있습니다.
- MySQL 8 클라이언트가 설치된 접속 호스트에서 DB의 private 네트워크에 도달할 수 있습니다.
- [Amazon RDS CA 번들](https://truststore.pki.rds.amazonaws.com/global/global-bundle.pem)과 실습 DB의 접속 계정을 준비합니다. 네트워크 접근을 위해 DB를 공개할 필요는 없습니다. <a href="#ref-tls">[9]</a>

### ① 현재 역할, AZ, 승격 우선순위를 기록합니다

```bash
export AWS_REGION='ap-northeast-2'
export AURORA_CLUSTER='aurora-failover-demo'
export AURORA_TARGET='demo-b'
export AWS_PAGER=''

aws rds describe-db-clusters \
  --region "$AWS_REGION" \
  --db-cluster-identifier "$AURORA_CLUSTER" \
  --query 'DBClusters[0].{Engine:Engine,Status:Status,WriterEndpoint:Endpoint,ReaderEndpoint:ReaderEndpoint,Members:DBClusterMembers[*].{Id:DBInstanceIdentifier,Writer:IsClusterWriter,Tier:PromotionTier}}' \
  --output json

aws rds describe-db-instances \
  --region "$AWS_REGION" \
  --filters "Name=db-cluster-id,Values=$AURORA_CLUSTER" \
  --query 'DBInstances[*].{Id:DBInstanceIdentifier,AZ:AvailabilityZone,Class:DBInstanceClass,Status:DBInstanceStatus,Version:EngineVersion}' \
  --output table
```

대상의 Engine이 `aurora-mysql`인지, `AURORA_TARGET`이 같은 클러스터의 Reader인지, 상태가 `available`이고 Writer와 다른 AZ인지 확인합니다. 쓰기 접속에는 첫 번째 명령의 `WriterEndpoint`를 사용합니다. `DBClusterMembers`의 `IsClusterWriter`로 Writer 역할을 확인할 수 있습니다. <a href="#ref-describe">[10]</a>

### ② 쓰기 관측을 시작합니다

실습 DB를 선택해 전용 테이블을 만듭니다. 각 요청에 고유한 ID를 넣어, 연결 오류가 났을 때 결과를 나중에 다시 조회할 수 있게 합니다.

```sql
CREATE TABLE failover_probe (
  request_id CHAR(36) PRIMARY KEY,
  created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
);
```

Writer Endpoint로 연결한 세션에서 아래 값을 확인합니다.

```sql
SELECT @@aurora_server_id AS instance_id,
       @@innodb_read_only AS read_only;
```

`@@aurora_server_id`는 실제 연결된 Aurora 인스턴스를 확인하는 데 사용합니다. 역할 판단에는 `innodb_read_only`와 클러스터의 Writer 정보를 함께 확인합니다. **`read_only=0`이나 `SELECT` 성공만으로 쓰기 복구가 검증되지는 않습니다.** 이 실습에서는 `INSERT`와 명시적 `COMMIT` 뒤 해당 요청 ID를 조회하는 것을 성공 기준으로 삼습니다. <a href="#ref-server-id">[11]</a>

글과 함께 제공하는 [관측 스크립트](/assets/data/aurora-mysql-failover/observe.py)는 매회 새 연결을 열어 쓰기와 조회를 수행하고 JSONL 로그를 남깁니다. 비밀번호는 MySQL의 login path를 통해 입력하며 명령행 인자로 넘기지 않습니다.

```bash
# 비밀번호를 대화식으로 입력합니다. 실습용 계정을 사용하세요.
mysql_config_editor set --login-path=aurora-demo \
  --user=demo_user --password

export AURORA_WRITER_ENDPOINT='조회한-Writer-Endpoint'
export AURORA_DATABASE='failover_demo'
export RDS_CA_BUNDLE='/path/to/global-bundle.pem'

python3 assets/data/aurora-mysql-failover/observe.py \
  --host "$AURORA_WRITER_ENDPOINT" \
  --database "$AURORA_DATABASE" \
  --ca "$RDS_CA_BUNDLE" \
  --login-path aurora-demo \
  --duration 180 \
  --output /tmp/aurora-manual.jsonl
```

이 스크립트는 **새 연결의 복구를 관측하는 기준선**입니다. 장시간 유지되는 기존 연결이나 애플리케이션의 connection pool을 시험하지는 않습니다. 실제 애플리케이션도 별도로 켜 두고 동일한 전환 중의 오류·재접속·쓰기 성공을 확인해야 합니다. 스크립트 사용법과 로그 해석은 [실습 README](/assets/data/aurora-mysql-failover/README.md)에 정리했습니다.

### ③ 다른 터미널에서 수동 전환을 요청합니다

```bash
date -u '+%Y-%m-%dT%H:%M:%SZ'

aws rds failover-db-cluster \
  --region "$AWS_REGION" \
  --db-cluster-identifier "$AURORA_CLUSTER" \
  --target-db-instance-identifier "$AURORA_TARGET" \
  --output json
```

`--target-db-instance-identifier`는 **같은 클러스터의 승격할 Reader**를 지정합니다. 이 글의 Aurora MySQL 실습에서 지원되는 옵션이며, RDS MySQL Multi-AZ DB cluster에는 같은 대상 지정 옵션을 적용할 수 없습니다. 명령이 응답했다고 데이터베이스와 애플리케이션의 전환이 모두 완료된 것은 아닙니다. <a href="#ref-api">[3]</a>

### ④ 역할 변경, 이벤트, 쓰기 성공을 함께 확인합니다

```bash
aws rds describe-db-clusters \
  --region "$AWS_REGION" \
  --db-cluster-identifier "$AURORA_CLUSTER" \
  --query 'DBClusters[0].DBClusterMembers[*].{Id:DBInstanceIdentifier,Writer:IsClusterWriter,Tier:PromotionTier}' \
  --output table

aws rds describe-events \
  --region "$AWS_REGION" \
  --source-type db-cluster \
  --source-identifier "$AURORA_CLUSTER" \
  --duration 60 \
  --query 'Events[*].{Time:Date,Message:Message}' \
  --output table
```

위 조회를 다시 실행해 대상 Reader의 `Writer`가 `true`로 바뀌었는지 확인합니다. 인스턴스 수준의 원인 분석이 필요하면 해당 인스턴스 ID로 `--source-type db-instance` 이벤트도 확인합니다. 최종 판정은 관측 로그와 실제 애플리케이션에서 **새 Writer로의 쓰기가 연속해서 성공하는지**입니다. 이벤트 조회 기능과 조회 기간은 `DescribeEvents` 문서를 참고합니다. <a href="#ref-describe">[10]</a> <a href="#ref-events">[12]</a>

실습 종료 뒤에는 전용 테이블과 생성한 실습 자원을 정리합니다. 원래 Writer로 되돌리려면 현재 Reader 역할과 정상 상태를 확인한 다음 다시 명시적으로 전환합니다. 이는 두 번째 전환이므로 연결에 다시 영향을 줄 수 있습니다. <a href="#ref-failover-manage">[7]</a>

<h2 id="automatic">5. 데모 2: 장애 감지 후 자동 Failover</h2>

이 영상은 Writer 장애를 Aurora가 감지하고, 다른 AZ의 사용 가능한 Reader를 승격하는 경로를 설명합니다. 장애 감지 과정과 이벤트·상태 표시는 모두 시뮬레이션입니다.

<figure class="video aurora-video">
<video controls preload="metadata" muted playsinline poster="/assets/videos/aurora-mysql-failover/automatic-poster.jpg" aria-label="Aurora MySQL 자동 Failover 동작 시뮬레이션">
<source src="/assets/videos/aurora-mysql-failover/automatic-failover.mp4" type="video/mp4">
<source src="/assets/videos/aurora-mysql-failover/automatic-failover.webm" type="video/webm">
<track kind="captions" src="/assets/videos/aurora-mysql-failover/automatic.ko.vtt" srclang="ko" label="한국어 단계 요약" default>
브라우저에서 영상을 재생할 수 없다면 <a href="/assets/videos/aurora-mysql-failover/automatic-failover.mp4">MP4 파일</a>을 내려받으세요.
</video>
<figcaption>자동 Failover 동작 데모 · 50초 · 무음 · 설명용 시뮬레이션. 자연 장애의 감지 시간이나 복구 성능을 측정한 영상이 아닙니다.</figcaption>
</figure>

동작 흐름은 다음과 같습니다.

1. Writer 인스턴스나 그 AZ의 문제로 쓰기 요청이 실패합니다.
2. Aurora가 문제를 감지하고 재시작·전환 등 복구 경로를 판단합니다.
3. Failover 경로에서는 사용 가능한 Reader를 Writer로 승격합니다.
4. 애플리케이션이 새 Writer에 재접속하고 쓰기를 재개합니다. <a href="#ref-ha">[1]</a> <a href="#ref-endpoints">[2]</a>

**자동 Failover를 켜는 SQL 명령이 별도로 필요한 구조는 아닙니다.** 자동 복구는 Aurora 기능이며 운영자가 준비할 것은 다른 AZ의 승격 가능한 Reader, 적절한 tier와 용량, 재접속 가능한 애플리케이션입니다. Reader가 없을 때도 복구 동작은 있지만, 준비된 Reader의 승격보다 Writer 재생성에 더 오래 걸릴 수 있습니다. <a href="#ref-ha">[1]</a>

AZ 장애를 대비한다고 특정 인스턴스로 무조건 바뀔 것이라고 가정해서는 안 됩니다. 장애 범위와 사용 가능한 후보가 중요합니다. 또한 다른 AZ로 전환하는 이 경로는 **한 리전 내부의 가용성**을 위한 것입니다. 리전 장애 대책은 Aurora Global Database 같은 별도의 설계를 검토해야 합니다. Global Database의 리전 간 복제는 비동기이므로 이 글의 로컬 Failover와 데이터 손실 조건을 같게 설명할 수 없습니다. <a href="#ref-ha">[1]</a> <a href="#ref-global">[13]</a>

<h2 id="fault-injection">6. 자동 Failover 시험에서 헷갈리기 쉬운 명령</h2>

### `ALTER SYSTEM CRASH`는 다른 Writer로의 전환 시험이 아닙니다

Aurora MySQL에는 다음과 같은 fault injection 쿼리가 있습니다.

```sql
ALTER SYSTEM CRASH INSTANCE;
```

AWS 문서는 이 쿼리에 대해 **Failover가 발생하지 않는다**고 명시합니다. 따라서 이를 실행하고 “Writer 프로세스를 죽였으니 자동으로 다른 AZ의 Reader가 승격된다”고 설명하면 잘못된 데모가 됩니다. 재시작 후 같은 인스턴스에서 요청이 다시 성공할 수 있으므로 인스턴스 ID까지 확인해야 합니다. AWS가 전환 시험에 안내하는 방법은 콘솔의 Failover 작업 또는 `failover-db-cluster`입니다. <a href="#ref-crash">[14]</a>

### FIS로 전환을 자동 실행해도 자연 장애 감지를 입증하지는 않습니다

AWS Fault Injection Service(FIS)의 `aws:rds:failover-db-cluster` 작업은 클러스터 Failover를 요청하는 기능입니다. 실험을 반복하고 애플리케이션 복구 경로를 검증하는 데 활용할 수 있습니다. 하지만 실험 도구가 전환 API를 호출한 것과 **서비스가 실제 장애를 감지해서 전환한 것**은 구분해서 보고해야 합니다. 전용 시험 환경에서 명확한 대상 선택과 중지 조건을 정한 뒤 사용하는 것이 이 글의 권고입니다. <a href="#ref-fis">[15]</a>

| 시험 방법 | 확인할 수 있는 것 | 그 시험만으로 확인할 수 없는 것 |
|---|---|---|
| 콘솔·CLI의 수동 Failover | Reader 승격, 재접속, 쓰기 복구 | 자연 장애 감지 시간, AZ 전체 장애 복구 |
| FIS의 클러스터 Failover 작업 | 실험으로 호출한 전환과 애플리케이션 대응 | 자연 장애 감지가 실제로 작동했는지 |
| `ALTER SYSTEM CRASH INSTANCE` | 인스턴스 crash와 재시작 경로의 요청 영향 | 다른 Reader로의 Failover |
| 장애 상황의 실제 이벤트·요청 로그 | 실제 복구 경로와 서비스 영향 | 기록하지 않은 구간이나 다른 장애 유형의 성능 |

이 표는 위 공식 기능 설명을 바탕으로 시험의 관측 범위를 나눈 것입니다. 일반 `reboot-db-instance`도 실행만으로 Reader 승격 시험이라고 간주하지 않습니다. <a href="#ref-crash">[14]</a> <a href="#ref-fis">[15]</a>

<h2 id="application">7. 전환은 끝났는데 애플리케이션이 복구되지 않을 때</h2>

### Writer Endpoint를 쓰고 DNS 캐시를 확인합니다

Writer 역할을 따라가야 하는 연결은 **클러스터의 Writer Endpoint**를 사용합니다. 인스턴스 Endpoint는 특정 인스턴스를 가리키므로, 이전 Writer가 Reader로 복귀한 뒤에도 그 주소만 고집하면 쓰기가 실패할 수 있습니다. Writer Endpoint 이름은 유지되지만 그 뒤의 대상은 바뀌며, 애플리케이션·운영체제의 DNS 캐시가 변경을 늦게 반영할 수 있습니다. <a href="#ref-endpoints">[2]</a> <a href="#ref-proxy">[16]</a>

재연결이 오래 걸릴 때에는 DNS 해석 결과, driver의 connect·socket timeout, connection pool의 불량 연결 제거 여부를 함께 확인하는 것이 이 글의 권고입니다. “DB는 복구됐는데 앱이 계속 예전 연결을 쓰는가”를 관측하려면 실제 앱의 인스턴스 ID와 오류 로그가 필요합니다.

### 진행 중 트랜잭션과 재시도를 설계합니다

전환 중의 연결 오류를 “거래는 반드시 실패했다”로 단정하면 안 됩니다. 특히 서버가 commit을 처리했지만 클라이언트가 응답을 받지 못한 경우에는 결과가 불명확할 수 있습니다. 이 글에서는 다음 처리를 권합니다.

- 실패한 연결을 버리고 새 연결로 작업합니다.
- 제한된 횟수의 retry에 backoff와 jitter를 적용하고 전체 요청 시간 제한을 둡니다.
- 단일 SQL만 이어서 재시도할지, 트랜잭션 전체를 다시 실행해야 할지 구분합니다.
- 주문·결제처럼 중복 실행이 문제인 작업에는 idempotency key나 유일한 업무 키를 사용합니다.
- 응답을 잃은 요청은 해당 키로 처리 여부를 조회하고 재실행을 판단합니다.

이 부분은 애플리케이션 구현 권고입니다. Aurora가 트랜잭션을 클라이언트 대신 안전하게 재실행해 준다는 뜻은 아닙니다. AWS의 RDS Proxy 문서도 트랜잭션에 연결된 세션의 Failover 예외와 재시도 처리를 설명합니다. <a href="#ref-proxy-transactions">[17]</a>

### Reader Endpoint도 알아 두어야 합니다

Reader Endpoint는 **쿼리마다가 아니라 새 연결 단위로** Reader에 분산합니다. Reader가 없으면 Writer로 연결되며, 전환 직후에는 새 Writer로 연결될 수 있는 짧은 구간도 있습니다. 이 주소만 보고 애플리케이션에 읽기 전용 권한이 강제된다고 가정하지 말고, 읽기 계정의 권한을 별도로 설계하는 것이 이 글의 권고입니다. <a href="#ref-reader">[18]</a> <a href="#ref-endpoints">[2]</a>

Aurora MySQL에서는 승격 대상 외의 Reader가 전환 중 읽기를 계속 처리할 수 있습니다. 읽기 연속성이 중요하다면 Reader를 한 대만 두는 구성과 여러 AZ에 여러 Reader를 두는 구성을 구분해 시험합니다. <a href="#ref-ha">[1]</a>

### RDS Proxy는 어디에 도움이 될까요?

DNS 캐시 때문에 Writer 변경을 늦게 따라가는 앱, 연결 생성이 많은 서비스, 많은 작은 connection pool을 운용하는 서비스에서 검토할 수 있습니다. RDS Proxy는 뒤쪽 인스턴스의 역할 변경을 추적하고 전환 때 Endpoint의 IP 변경에 의존하는 지연을 줄일 수 있습니다. 다만 진행 중 트랜잭션·세션 상태에 따라 오류 처리가 필요하고, 별도 비용과 추가 네트워크 경로가 생깁니다. 도입 여부는 자신의 앱으로 비교 시험합니다. <a href="#ref-proxy">[16]</a> <a href="#ref-proxy-transactions">[17]</a>

<h2 id="operations">8. 언제 수동 전환을 쓰고, 어떤 구성을 선택할까</h2>

수동 Failover는 **전환 시점을 선택해야 하는 작업**에 유용합니다. 자동 Failover는 **예상하지 못한 장애의 복구 경로**입니다. 다음은 이 글의 운영 권고입니다.

| 상황 | 권하는 접근 | 확인할 내용 |
|---|---|---|
| 서비스 오픈 전 복구 연습 | 다른 AZ의 Reader를 지정한 수동 전환 | 기존 연결, connection pool, retry, 쓰기 오류 구간 |
| 계획된 Writer 사양 변경·역할 이동 | 새 역할을 맡을 Reader를 준비하고 작업 절차에 따라 수동 전환 검토 | 승격 후 용량, 변경 작업 자체의 제약, 전환 영향 |
| Writer의 예기치 않은 장애 | Aurora 자동 복구 상태와 이벤트를 먼저 확인 | 현재 Writer, 후보 정상 여부, 앱의 쓰기 복구 |
| Writer AZ 장애에 대비 | 다른 AZ에 적어도 한 Reader 준비 | 앱·접속 호스트·네트워크도 장애 AZ에만 묶여 있지 않은지 |
| 읽기도 중단을 줄여야 함 | 여러 AZ의 Reader와 읽기 연결 전략 검토 | 승격 후 남는 Reader 용량과 접속 오류 |
| 리전 전체 장애에 대비 | 리전 간 DR 설계를 별도로 검토 | Global Database, 업무 RPO·RTO, 승격·복구 절차 |

계획된 컴퓨트 변경에서 Failover를 활용할 수 있다는 근거는 AWS의 RDS Proxy 운영 설명에 있습니다. 실제 변경은 사용 중인 Aurora 버전과 인스턴스 변경 문서에 맞춰 진행합니다. 이 글의 수동 전환을 엔진 업그레이드 전체를 대체하는 절차로 적용하지 않습니다. <a href="#ref-proxy">[16]</a>

자동 복구 중 명령을 반복해서 전환을 추가 요청하기 전에 현재 상태와 이벤트를 확인하는 것이 좋습니다. 이전 인스턴스가 회복되었다는 이유만으로 곧바로 원래 Writer로 돌아가야 할 필요도 없습니다. 원래 위치로 되돌리는 작업은 필요성과 영향을 검토한 뒤 수행할 별도의 전환으로 취급합니다.

**실무 출발점으로는 서로 다른 AZ의 Writer 1대와 Reader 1대를 권합니다.** 더 높은 읽기 연속성이나 후보 여유가 필요하면 세 번째 AZ의 Reader를 검토합니다. Reader 비용을 줄이더라도 승격 후 쓰기 용량을 지나치게 작게 잡으면 복구 직후 과부하가 생길 수 있습니다. 이 권고는 아래 조건을 함께 만족하는 구성을 뜻합니다.

- 승격 후보가 정상이며 Writer와 다른 AZ에 있습니다.
- Writer Endpoint를 사용하고, 새 연결에서 DNS 변경을 반영할 수 있습니다.
- 실제 앱의 재접속·재시도·중복 방지 처리를 시험했습니다.
- 애플리케이션과 접속 경로도 장애 AZ 밖에서 사용할 수 있습니다.
- 실습 결과를 가지고 업무의 중단 허용 시간과 용량을 판단합니다.

<h2 id="references">9. 복구 시간과 데이터 확인, 참고 자료</h2>

AWS 문서는 기존 Aurora Reader를 승격하는 복구를 **보통 60초 미만, 흔히 30초 미만**으로 안내합니다. Reader가 없어 Writer를 재생성하는 일반 인스턴스 장애의 복구는 **보통 10분 미만**으로 설명합니다. 이 숫자는 문서의 일반적인 안내이며, 이 글의 실측 결과나 모든 장애에서의 시간 보장이 아닙니다. AZ 전체 장애에서 단일 인스턴스 구성에 적용할 복구 보장으로도 해석하지 않습니다. <a href="#ref-ha">[1]</a>

애플리케이션의 복구 시간은 장애 감지, 인스턴스 복구·승격, Endpoint 반영, 재접속, 첫 성공한 업무 쓰기까지의 영향을 받습니다. 이 글에서는 아래 시점을 기록하기를 권합니다.

| 기록할 시점·값 | 판단 목적 |
|---|---|
| 수동 요청 시각 또는 실제 장애의 관측 시각 | 시험의 시작점과 장애 감지 범위 구분 |
| 마지막 성공, 첫 실패, 마지막 실패, 첫 연속 성공 | 클라이언트가 겪은 실패 구간 확인 |
| 새 Writer가 확인된 시각과 실제 연결 인스턴스 ID | 제어 영역의 역할 변경과 클라이언트 복구 구분 |
| 새 Writer에서 `INSERT` 후 조회 성공 | SQL 쓰기와 데이터 가시성 확인 |
| 요청 ID별 결과와 중복·누락 여부 | 응답을 잃은 작업의 최종 결과 확인 |
| 엔진 버전, 사양, AZ, 부하, driver·pool 설정 | 다음 시험과 비교할 조건 기록 |

1초 간격의 probe에는 관측 해상도 한계가 있고, 새 연결을 매번 만드는 CLI 결과는 실제 애플리케이션의 RTO를 대신하지 않습니다. 관측 스크립트의 timeout과 요청 처리 시간도 표본 간격에 들어갑니다.

스토리지 복제는 AZ 장애에서 데이터를 보호하는 장치입니다. 이 보호를 잘못된 `DELETE`, 논리적 데이터 훼손, 응답이 끊긴 거래의 결과 판단까지 해결해 주는 기능으로 확대해서 설명해서는 안 됩니다. 데이터 복구를 위해서는 백업·PITR 정책과 복원 시험이 별도로 필요합니다. <a href="#ref-ha">[1]</a> <a href="#ref-backup">[19]</a>

Aurora MySQL의 가용성을 판단할 때에는 **다른 AZ의 승격 가능한 Reader가 있는가, 그리고 전환 뒤 애플리케이션이 실제로 다시 쓰는가**를 함께 확인합니다. 수동 Failover는 이 경로를 미리 연습하는 수단이고, 자동 Failover는 준비된 구성 위에서 장애를 복구하는 기능입니다.

### 실습 파일과 영상

- [관측 스크립트 및 실습 안내](/assets/data/aurora-mysql-failover/README.md)
- [쓰기 관측 Python 스크립트](/assets/data/aurora-mysql-failover/observe.py)
- [수동 Failover MP4](/assets/videos/aurora-mysql-failover/manual-failover.mp4) · [WebM](/assets/videos/aurora-mysql-failover/manual-failover.webm) · [한국어 설명 자막](/assets/videos/aurora-mysql-failover/manual.ko.vtt)
- [자동 Failover MP4](/assets/videos/aurora-mysql-failover/automatic-failover.mp4) · [WebM](/assets/videos/aurora-mysql-failover/automatic-failover.webm) · [한국어 설명 자막](/assets/videos/aurora-mysql-failover/automatic.ko.vtt)

### AWS 공식 참고 자료

<ol>
<li id="ref-ha"><a href="https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/Concepts.AuroraHighAvailability.html">High availability for Amazon Aurora</a> — 스토리지 복제, AZ 배치, 자동 복구, promotion tier, 일반적인 복구 시간.</li>
<li id="ref-endpoints"><a href="https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/Aurora.Overview.Endpoints.html">Amazon Aurora endpoint connections</a> — 역할 변경을 따라가는 Endpoint와 재접속.</li>
<li id="ref-api"><a href="https://docs.aws.amazon.com/AmazonRDS/latest/APIReference/API_FailoverDBCluster.html">FailoverDBCluster API</a> — 전환 요청과 대상 지정, 배포 방식별 제약.</li>
<li id="ref-overview"><a href="https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/Aurora.Overview.html">Amazon Aurora DB clusters</a> — 컴퓨트와 공유 클러스터 볼륨의 구조.</li>
<li id="ref-rds-instance"><a href="https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/Concepts.MultiAZSingleStandby.html">Multi-AZ DB instance deployments</a> — 읽기를 처리하지 않는 standby.</li>
<li id="ref-rds-cluster"><a href="https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/multi-az-db-clusters-concepts.html">Multi-AZ DB cluster deployments</a> — Writer와 읽기 가능한 standby.</li>
<li id="ref-failover-manage"><a href="https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/aurora-failover.html">Failing over an Amazon Aurora DB cluster</a> — Failover 작업과 기존 Writer의 역할.</li>
<li id="ref-promote"><a href="https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/AuroraMySQL.Replication.CrossRegion.html">Cross-Region replication with Aurora MySQL</a> — read replica cluster의 독립 클러스터 승격과 구분.</li>
<li id="ref-tls"><a href="https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/UsingWithRDS.SSL.html">Using SSL/TLS to encrypt a connection to a DB cluster</a> — RDS CA와 TLS 연결.</li>
<li id="ref-describe"><a href="https://docs.aws.amazon.com/cli/latest/reference/rds/describe-db-clusters.html">describe-db-clusters</a> — Endpoint·Writer·promotion tier 조회.</li>
<li id="ref-server-id"><a href="https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/AuroraMySQL.Compare-80-v3.html#AuroraMySQL.mysql80-server-id">Finding the database server ID</a> — 인스턴스 식별 변수.</li>
<li id="ref-events"><a href="https://docs.aws.amazon.com/cli/latest/reference/rds/describe-events.html">describe-events</a> — 클러스터·인스턴스 이벤트 확인.</li>
<li id="ref-global"><a href="https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/aurora-global-database.html">Using Amazon Aurora Global Database</a> — 리전 간 복제와 재해 복구.</li>
<li id="ref-crash"><a href="https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/AuroraMySQL.Managing.FaultInjectionQueries.html">Testing Aurora MySQL using fault injection queries</a> — crash 쿼리는 Failover를 일으키지 않음.</li>
<li id="ref-fis"><a href="https://docs.aws.amazon.com/fis/latest/userguide/fis-actions-reference.html#rds-actions-reference">AWS FIS Amazon RDS actions</a> — 실험에서 호출하는 Failover 작업.</li>
<li id="ref-proxy"><a href="https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/rds-proxy-best-practices.usage-scenarios.html">RDS Proxy common usage scenarios</a> — DNS·연결 관리와 가용성.</li>
<li id="ref-proxy-transactions"><a href="https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/rds-proxy.howitworks.html">How RDS Proxy works</a> — 세션·트랜잭션과 Failover 처리.</li>
<li id="ref-reader"><a href="https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/Aurora.Endpoints.Reader.html">Reader endpoints for Amazon Aurora</a> — 연결 단위 분산과 Writer 연결 예외.</li>
<li id="ref-backup"><a href="https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/Aurora.Managing.Backups.html">Backing up and restoring an Aurora DB cluster</a> — 백업·PITR와 복원.</li>
</ol>
