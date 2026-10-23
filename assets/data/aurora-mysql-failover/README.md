# Aurora MySQL Failover 실습

이 디렉터리는 블로그의 **실제 환경용 재현 도구**입니다. 게시된 두 영상은 설명용 시뮬레이션입니다. 실제 클러스터에서 Failover나 성능 측정을 실행한 결과는 포함하지 않습니다.

`observe.py`는 AWS API를 호출하지 않습니다. 이미 준비한 Writer Endpoint에 SQL을 실행하고 결과를 기록합니다. 수동 전환 요청은 블로그의 CLI 명령을 다른 터미널에서 직접 실행합니다.

## 준비

- 서로 다른 AZ의 Writer와 정상 Reader가 있는 전용 Aurora MySQL 실습 클러스터
- private 네트워크에서 DB로 접속 가능한 호스트
- Python 3.9 이상, MySQL 8의 `mysql` 및 `mysql_config_editor`
- DB 호스트 이름을 검증할 RDS CA PEM 번들
- 전용 DB `failover_demo`와 실습 계정. 계정에 해당 DB의 테이블 생성·INSERT·SELECT 권한이 필요합니다.

MariaDB 클라이언트는 TLS 옵션이 달라 이 스크립트의 대상이 아닙니다.

접속 비밀번호를 소스나 명령행에 쓰지 않고 대화식으로 입력합니다.

```bash
mysql_config_editor set --login-path=aurora-demo \
  --user=demo_user --password
```

login path 파일은 MySQL 클라이언트가 로컬에 보관하는 난독화된 자격 증명이며 강력하게 암호화된 비밀 저장소가 아닙니다. 실습 계정을 사용하고 호스트의 파일 접근을 통제합니다. MySQL 공식 설명: [mysql_config_editor](https://dev.mysql.com/doc/refman/8.0/en/mysql-config-editor.html).

AWS 공식 CA 번들: [global-bundle.pem](https://truststore.pki.rds.amazonaws.com/global/global-bundle.pem).

```bash
export AURORA_WRITER_ENDPOINT='조회한-Writer-Endpoint'
export AURORA_DATABASE='failover_demo'
export RDS_CA_BUNDLE='/path/to/global-bundle.pem'

mysql --login-path=aurora-demo \
  --host="$AURORA_WRITER_ENDPOINT" --database="$AURORA_DATABASE" \
  --ssl-mode=VERIFY_IDENTITY --ssl-ca="$RDS_CA_BUNDLE"
```

전용 DB에서 다음 테이블을 만듭니다.

```sql
CREATE TABLE failover_probe (
  request_id CHAR(36) PRIMARY KEY,
  created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
);
```

## 관측

저장소 루트에서 실행합니다. 별도의 Python 패키지는 필요하지 않습니다.

```bash
python3 assets/data/aurora-mysql-failover/observe.py \
  --host "$AURORA_WRITER_ENDPOINT" \
  --database "$AURORA_DATABASE" \
  --ca "$RDS_CA_BUNDLE" \
  --login-path aurora-demo \
  --duration 180 \
  --output /tmp/aurora-manual.jsonl
```

기존 출력 파일은 덮어쓰지 않습니다. 기본값은 요청 뒤 대기 1초, MySQL 연결 timeout 3초, 한 요청의 전체 timeout 8초입니다. 요청 처리 시간도 표본 간격에 포함되므로 정확한 1Hz 부하 도구가 아닙니다.

동시에 실제 앱을 실행하고 블로그의 `failover-db-cluster`를 별도 터미널에서 호출합니다. 새 Writer 확인, 기존 앱의 재접속, 쓰기 연속 성공을 함께 기록합니다.

## 로그 해석

각 JSONL 레코드는 시작·완료 시각, 요청 UUID, 처리 시간, 확인된 인스턴스 ID·읽기 전용 상태, 결과를 포함합니다.

- `confirmed_write`: 새 연결에서 명시적인 트랜잭션의 INSERT·COMMIT이 완료되고 같은 연결에서 해당 UUID를 조회했습니다.
- `unconfirmed`: 클라이언트 오류, timeout 또는 예상 밖 출력입니다. **서버에서 INSERT가 실패했다는 확정 판정이 아닙니다.**
- `connection_mode: fresh`: 매 요청마다 새 MySQL 프로세스·새 연결을 사용합니다. 유지된 연결과 앱 connection pool의 동작은 별도로 시험해야 합니다.

응답을 잃은 요청은 복구 뒤 `request_id`로 조회합니다. UUID를 SQL 문자열 안에 직접 입력할 때에는 로그에서 생성된 UUID 값만 사용합니다.

실행 중 Ctrl+C로 중단해도 진행 중 요청의 UUID를 `unconfirmed` 레코드로 남깁니다.

```sql
SELECT request_id, created_at
FROM failover_probe
WHERE request_id = '로그에-기록된-UUID';
```

“첫 성공”만 보지 말고 여러 번 연속 성공했는지 확인합니다. 표본 실패 구간과 요청 시각의 차이를 측정할 때에는 관측 간격·timeout을 함께 보고합니다. 이 로그만으로 실제 업무 RTO나 데이터 손실 여부를 판정하지 않습니다.

## 정리

관측을 종료한 뒤 실습 테이블을 삭제하고 실습용 클러스터·접속 호스트 등 생성한 자원을 정리합니다. 이 디렉터리에는 자원 생성·삭제를 자동 실행하는 스크립트가 없습니다.

```sql
DROP TABLE failover_probe;
```

사용 후 실습용 login path가 더 이상 필요하지 않으면 제거합니다.

```bash
mysql_config_editor remove --login-path=aurora-demo
```

## 영상 재제작

영상 소스는 `assets/videos/aurora-mysql-failover/manual/`와 `automatic/`의 HyperFrames 프로젝트입니다. 각 프로젝트에서 `npm run check`, `npm run render`를 실행할 수 있습니다. CLI는 `hyperframes@0.8.181`로 고정했습니다.

영상은 무음이며 한국어 설명이 화면에 포함되어 있습니다. MP4와 WebM 두 형식으로 제공하고 별도로 단계 제목을 요약한 WebVTT 자막을 제공합니다. 자막은 영상 상단의 여백에 표시합니다. 글의 각 영상 아래에 순서를 텍스트로 풀어 썼습니다.

WebM 대체 소스는 렌더링된 MP4에서 다음과 같이 변환합니다.

```bash
ffmpeg -i manual-failover.mp4 -an \
  -c:v libvpx-vp9 -crf 32 -b:v 0 -row-mt 1 -cpu-used 4 \
  manual-failover.webm
```
