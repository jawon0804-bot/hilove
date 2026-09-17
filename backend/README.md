# 방명록 저장소 (morning-80308 Firestore)

페이지(`index.html`)는 방명록을 Google 시트(Apps Script) 대신 **morning-80308 Firestore의 `guestbook` 컬렉션**에 바로 쓴다.
엑셀은 내보내기 주소(`export/`)에서 CSV로 받아 온다.

```
[페이지] ──쓰기(REST, 규칙 검사)──▶ Firestore guestbook ◀──읽기(서버 권한)── [hilove-export (Cloud Run)] ──CSV──▶ [엑셀 파워 쿼리]
```

## 기록 규칙 (`firestore.rules`)

| 동작 | 누가 | 조건 |
|---|---|---|
| 등록 | 누구나 | 상태 `교육미이수`로만. 이름 1~30자, 연락처 숫자·하이픈 9~20자, 구분 5종, 상세 내용 1~100자. 등록·수정 시각은 서버 시각 |
| 완료 | 누구나 | 그 기록이 `교육미이수`이고 **등록한 날(한국 시간)** 안에, **상태 칸만** `교육완료`/`방문확인`으로 |
| 재등록 | 누구나 | 같은 날, 아직 `교육미이수`인 기록의 소속·주소·구분·상세 내용만 |
| 조회·삭제 | 아무도 | 브라우저에서는 불가. 엑셀은 내보내기 주소로 |

- 페이지는 방금 만든 기록의 ID를 기억해 두었다가 완료할 때 그 기록만 고친다(날짜·이름·연락처로 찾지 않음).
- 같은 기기에서 오늘 등록하고 끝내지 않은 기록은 `localStorage`(`hilove.pendingVisit`)에 남아, 다시 등록하면 새 기록 대신 그 기록을 고쳐 쓴다.
- ⚠️ 이 파일을 배포하면 **프로젝트 규칙 전체**가 바뀐다. `morning_brief`(now-morning 서버)는 서버 권한이라 영향이 없다.

검증(배포 없이 실제 규칙 엔진): `node backend/test-rules.js`

배포: `cd backend && firebase deploy --only firestore:rules --project morning-80308`

## 내보내기 (`export/`)

- 주소: `https://hilove-export-n45f6cr3iq-du.a.run.app/?key=<키>` (Cloud Run `hilove-export`, asia-northeast3) — 선택: `&from=2026-09-01&to=2026-09-30` (한국 날짜, 양끝 포함)
- 열: NO · 날짜 · 시간 · 소속 · 이름 · 연락처 · 주소 · 구분 · 방문내용 · 교육확인 (UTF-8 CSV)
- NO는 전체 기록을 등록 순서로 센 번호라 기간을 좁혀도 같다. 날짜·시간은 등록 시각(한국 시간).
- 키: Secret Manager `hilove-export-key` → 환경변수 `EXPORT_KEY`. 헤더 `X-Export-Key`로 보내도 된다.
- 실행 계정: `hilove-export@morning-80308.iam.gserviceaccount.com` (Firestore 읽기 + 이 비밀 하나만)

검사: `python -m pytest backend/export -q` (functions-framework, pytest 필요)

배포:
```
gcloud run deploy hilove-export --source backend/export --function export_csv --base-image python312 \
  --region asia-northeast3 --service-account hilove-export@morning-80308.iam.gserviceaccount.com \
  --set-secrets EXPORT_KEY=hilove-export-key:latest --allow-unauthenticated --max-instances 2 --project morning-80308
```

### 엑셀에서 받기
데이터 → 웹에서 → 주소 붙여넣기 → (CSV 미리보기) 로드. 이후 **모두 새로 고침**으로 갱신.
