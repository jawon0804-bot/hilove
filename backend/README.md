# 방명록 저장소 (morning-80308 Firestore)

페이지(`index.html`)는 방명록을 Google 시트(Apps Script) 대신 **morning-80308 Firestore의 `guestbook` 컬렉션**에 바로 쓴다.
퇴실(`out.html`, 출입구 QR·NFC)과 관리자 화면(`out.html`의 ⚙)은 서버(`export/`, Cloud Run `hilove-export`)를 거친다.
엑셀은 같은 서버의 내보내기 주소에서 CSV로 받아 온다.

```
[index.html] ──등록·완료(REST, 규칙 검사)──▶ Firestore guestbook ◀──읽기·쓰기(서버 권한)── [hilove-export (Cloud Run)]
[out.html]   ──퇴실·관리자(POST)─────────────────────────────────────────────────────────▶      │
[엑셀 파워 쿼리] ◀──CSV──────────────────────────────────────────────────────────────────────────┘
```

## 기록 규칙 (`firestore.rules`) — 브라우저가 직접 쓰는 것

| 동작 | 누가 | 조건 |
|---|---|---|
| 등록 | 누구나 | 상태 `교육미이수`로만. 이름 1~30자, 연락처 숫자·하이픈 9~20자, 구분 5종, 상세 내용 0~100자(선택). 등록·수정 시각은 서버 시각 |
| 완료 | 누구나 | 그 기록이 `교육미이수`이고 **등록한 날(한국 시간)** 안에, **상태 칸만** `교육완료`/`방문확인`으로 |
| 재등록 | 누구나 | 같은 날, 아직 `교육미이수`인 기록의 소속·주소·구분·상세 내용만 |
| 조회·삭제 | 아무도 | 브라우저에서는 불가 |

- 퇴실 시각(`exitAt`·`exitBy`)과 전달사항(`note`)은 규칙상 브라우저가 못 쓴다 — 서버만 쓴다.
- `hilove_admin/config`(비밀번호 해시), `hilove_admin_locks`(오입력 잠금)도 전체 차단 규칙에 걸려 서버만 접근한다.
- 페이지는 방금 만든 기록의 ID를 기억해 두었다가 완료할 때 그 기록만 고친다. `localStorage`:
  - `hilove.pendingVisit` — 오늘 등록하고 아직 완료 안 한 기록(다시 등록하면 새 기록 대신 고쳐 씀). 완료하면 지운다.
  - `hilove.todayVisit` — 이 기기의 오늘 기록. 완료 후에도 남아 `out.html`이 이름 입력 없이 퇴실시킨다(18시간).
- ⚠️ 이 파일을 배포하면 **프로젝트 규칙 전체**가 바뀐다. `morning_brief`(now-morning 서버)는 서버 권한이라 영향이 없다.

검증(배포 없이 실제 규칙 엔진): `node backend/test-rules.js`

배포: `cd backend && firebase deploy --only firestore:rules --project morning-80308`

## 서버 (`export/`, Cloud Run `hilove-export`, asia-northeast3)

주소: `https://hilove-export-n45f6cr3iq-du.a.run.app`

| 경로 | 누가 | 내용 |
|---|---|---|
| `GET /` | 엑셀 | CSV. **기본(Basic) 인증** — 공용 비밀번호(`CSV_PASSWORD`), 사용자 이름은 아무거나. 선택 `?from=2026-09-01&to=2026-09-30`(한국 날짜, 양끝 포함). 자격 증명이 없으면 401 + `WWW-Authenticate: Basic`(엑셀이 이걸 보고 입력창을 띄운다). 같은 IP에서 5번 틀리면 15분 잠금(관리자 로그인과 따로 셈) |
| `GET /ping` | 퇴실 페이지 | 서버 깨우기(204) |
| `POST /tasks/purge` | Cloud Scheduler `hilove-purge` | 보유 기간(1년) 지난 방명록 파기. `hilove-export@` 계정 OIDC 토큰만 통과 |
| `POST /checkout` | 누구나 | `{id}` 또는 `{name, phone}` + 선택 `{note}`. 등록 후 18시간 안의 기록만. 퇴실 시각은 서버 시각, 이미 퇴실했으면 시각은 두고 전달사항만 덧붙임 |
| `POST /admin/login` | 관리자 | `{password}` → 12시간 토큰. 같은 IP에서 5번 틀리면 15분 잠금 |
| `POST /admin/visits` · `/admin/update` · `/admin/delete` · `/admin/password` | 관리자 | `Authorization: Bearer <토큰>`. 그날 목록 / 퇴실 시간·전달사항·항목 수정 / 삭제 / 비밀번호 변경(기존 로그인 모두 무효) |

- 관리자 ID는 `admin` 하나. **초기 비밀번호 `1234`**(설정 문서가 없을 때) — 첫 로그인 후 ⚙ → 비밀번호에서 바꾼다. 비밀번호는 PBKDF2 해시로만 저장.
- 토큰 서명은 `SESSION_SECRET`에서 파생한다(엑셀 비밀번호·키와 별개). 이 값을 바꾸면 모든 관리자 로그인이 풀린다. `SESSION_SECRET`이 없으면 전환 기간용으로 `EXPORT_KEY`에서 파생하는데, 그동안은 CSV 키를 아는 사람이 토큰을 만들 수 있다 — 반드시 `SESSION_SECRET`을 설정할 것.
- 예전 `?key=<키>`(헤더 `X-Export-Key`) 방식은 `EXPORT_KEY`가 있는 동안만 통과한다. 엑셀을 모두 비밀번호 방식으로 옮긴 뒤 `EXPORT_KEY`를 비워 닫는다.
- 브라우저 호출은 `ALLOWED_ORIGINS`(기본 `https://jawon0804-bot.github.io`)에만 CORS를 연다.

### CSV 열 (A~K)
NO · 날짜 · 시간 · 소속 · 이름 · 연락처 · 주소 · 구분 · 방문내용 · 교육확인 · **전달사항**

- NO는 전체 기록을 등록 순서로 센 번호라 기간을 좁혀도 같다. 날짜는 등록일(한국 시간).
- 시간: `10:00 ~ 16:00`(관리자가 넣은 시간도 같은 모양, 누가 넣었는지는 `exitBy`에만) · 날을 넘기면 `익일 01:00` · 오늘 미퇴실 `10:00 ~` · 지난 날 미퇴실 `10:00 ~ 미확인`

### 실행 계정 · 배포
실행 계정 `hilove-export@morning-80308.iam.gserviceaccount.com`: `roles/datastore.user`(Firestore 읽기·쓰기) + 아래 비밀 읽기.

| 비밀 (Secret Manager) | 환경변수 | 용도 |
|---|---|---|
| `hilove-csv-password` | `CSV_PASSWORD` | 엑셀 공용 비밀번호 |
| `hilove-session-secret` | `SESSION_SECRET` | 관리자 토큰 서명 (긴 랜덤 문자열) |
| `hilove-export-key` | `EXPORT_KEY` | 예전 `?key=` 방식(정리되면 제거) |

검사: `python -m pytest backend/export -q` (functions-framework, google-cloud-firestore, pytest 필요)

```
gcloud run deploy hilove-export --source backend/export --function export_csv --base-image python312 \
  --region asia-northeast3 --service-account hilove-export@morning-80308.iam.gserviceaccount.com \
  --set-secrets EXPORT_KEY=hilove-export-key:latest,CSV_PASSWORD=hilove-csv-password:latest,SESSION_SECRET=hilove-session-secret:latest \
  --allow-unauthenticated --max-instances 2 --project morning-80308
```

### 보유 기간 파기 (개인정보 1년)
등록 화면 동의서의 "수집일로부터 1년"을 지키려고 매일 새벽 Cloud Scheduler가 `/tasks/purge`를 부른다.
한국 날짜로 등록일부터 1년이 지난 다음 날 0시부터 지운다(2025-11-13 등록 → 2026-11-14 새벽 삭제). 기간은 `logic.RETENTION_YEARS`.
삭제 건수는 Cloud Logging에 `{"purge": {...}}`로 남는다. 엑셀로 받아 둔 사본은 지워지지 않으니 엑셀 쪽도 1년 넘은 행을 정리할 것.

```
gcloud scheduler jobs create http hilove-purge --location asia-northeast3 --schedule "30 3 * * *" --time-zone Asia/Seoul \
  --uri https://hilove-export-n45f6cr3iq-du.a.run.app/tasks/purge --http-method POST \
  --oidc-service-account-email hilove-export@morning-80308.iam.gserviceaccount.com \
  --oidc-token-audience https://hilove-export-n45f6cr3iq-du.a.run.app --project morning-80308
```

### 엑셀에서 받기
데이터 → 웹에서 → 주소(`https://hilove-export-n45f6cr3iq-du.a.run.app/`, **키 없이**) 붙여넣기 → 인증 창에서 **기본** 선택 → 사용자 이름은 아무거나(예: `hilove`)·비밀번호는 공용 비밀번호 → 연결 → (CSV 미리보기) 로드.
비밀번호는 파일이 아니라 각자 PC의 엑셀 자격 증명에 저장되므로 파일을 공유해도 따라가지 않는다. 이후 **모두 새로 고침**으로 갱신(비밀번호 재입력 없음).
비밀번호를 바꾸면 각자 데이터 → 데이터 원본 설정 → 사용 권한 지우기 후 새로 입력.
열이 늘면 쿼리의 `Csv.Document(... Columns=N ...)` 숫자를 맞춘다(고급 편집기).

## QR·NFC
출입구에 `https://jawon0804-bot.github.io/hilove/out.html` 을 담는다(QR 코드, NFC 태그는 NDEF URL 레코드).
