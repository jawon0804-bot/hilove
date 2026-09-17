// backend/test-rules.js
// firestore.rules를 Firebase Rules API(TestRuleset)로 "실제 규칙 엔진에" 돌려서 검증한다. 배포는 하지 않는다.
//
// 실행:  node backend/test-rules.js   (gcloud 로그인 필요, 실패 시 종료 코드 1)
const fs = require("fs");
const path = require("path");
const { execSync } = require("child_process");

const PROJECT = "morning-80308";
const doc = (p) => `/databases/(default)/documents/${p}`;
const ID = "AbCdEfGhIjKlMnOpQrSt";

// 한국 시간 2026-09-17 10:00
const NOW = "2026-09-17T01:00:00Z";
const EARLIER_TODAY = "2026-09-16T15:30:00Z"; // 09-17 00:30 KST
const YESTERDAY = "2026-09-16T05:00:00Z";     // 09-16 14:00 KST

const person = { company: "맥서브", name: "홍길동", phone: "010-1234-5678", address: "울산 남구", category: "방문", reason: "시설팀 미팅" };
const newDoc = (over = {}) => ({ ...person, status: "교육미이수", createdAt: NOW, updatedAt: NOW, ...over });
const pending = (over = {}) => ({ ...person, status: "교육미이수", createdAt: EARLIER_TODAY, updatedAt: EARLIER_TODAY, ...over });
const without = (o, k) => { const c = { ...o }; delete c[k]; return c; };

// [이름, 기대, 메서드, 경로, 요청 시각, 쓰려는 문서(request.resource.data), 기존 문서(resource.data)]
const cases = [
  ["등록: 방문", "ALLOW", "create", doc(`guestbook/${ID}`), NOW, newDoc(), null],
  ["등록: 작업, 소속·주소 빈칸", "ALLOW", "create", doc(`guestbook/${ID}`), NOW, newDoc({ category: "작업", company: "", address: "" }), null],
  ["등록: 처음부터 교육완료", "DENY", "create", doc(`guestbook/${ID}`), NOW, newDoc({ status: "교육완료" }), null],
  ["등록: 모르는 칸 추가", "DENY", "create", doc(`guestbook/${ID}`), NOW, newDoc({ memo: "x" }), null],
  ["등록: 상세 내용 없음", "DENY", "create", doc(`guestbook/${ID}`), NOW, newDoc({ reason: "" }), null],
  ["등록: 상세 내용 칸 자체가 없음", "DENY", "create", doc(`guestbook/${ID}`), NOW, without(newDoc(), "reason"), null],
  ["등록: 상세 내용 101자", "DENY", "create", doc(`guestbook/${ID}`), NOW, newDoc({ reason: "가".repeat(101) }), null],
  ["등록: 이름 없음", "DENY", "create", doc(`guestbook/${ID}`), NOW, newDoc({ name: "" }), null],
  ["등록: 연락처에 글자", "DENY", "create", doc(`guestbook/${ID}`), NOW, newDoc({ phone: "010-abcd-5678" }), null],
  ["등록: 연락처 너무 짧음", "DENY", "create", doc(`guestbook/${ID}`), NOW, newDoc({ phone: "010-12" }), null],
  ["등록: 없는 구분", "DENY", "create", doc(`guestbook/${ID}`), NOW, newDoc({ category: "기타" }), null],
  ["등록: 등록 시각을 지어냄", "DENY", "create", doc(`guestbook/${ID}`), NOW, newDoc({ createdAt: YESTERDAY }), null],
  ["등록: 문서 ID 형식 틀림", "DENY", "create", doc("guestbook/abc"), NOW, newDoc(), null],

  ["완료: 교육완료", "ALLOW", "update", doc(`guestbook/${ID}`), NOW, pending({ status: "교육완료", updatedAt: NOW }), pending()],
  ["완료: 방문확인", "ALLOW", "update", doc(`guestbook/${ID}`), NOW, pending({ status: "방문확인", updatedAt: NOW }), pending()],
  ["완료: 23시에 완료(00:30 등록)", "ALLOW", "update", doc(`guestbook/${ID}`), "2026-09-17T14:00:00Z", pending({ status: "교육완료", updatedAt: "2026-09-17T14:00:00Z" }), pending()],
  ["완료: 다음날 00:10에 완료", "DENY", "update", doc(`guestbook/${ID}`), "2026-09-17T15:10:00Z", pending({ status: "교육완료", updatedAt: "2026-09-17T15:10:00Z" }), pending()],
  ["완료: 어제 등록한 기록", "DENY", "update", doc(`guestbook/${ID}`), NOW, pending({ status: "교육완료", createdAt: YESTERDAY, updatedAt: NOW }), pending({ createdAt: YESTERDAY, updatedAt: YESTERDAY })],
  ["완료: 이미 교육완료인 기록을 방문확인으로", "DENY", "update", doc(`guestbook/${ID}`), NOW, pending({ status: "방문확인", updatedAt: NOW }), pending({ status: "교육완료" })],
  ["완료: 상태와 이름을 같이 바꿈", "DENY", "update", doc(`guestbook/${ID}`), NOW, pending({ status: "교육완료", name: "김철수", updatedAt: NOW }), pending()],
  ["완료: 없는 상태값", "DENY", "update", doc(`guestbook/${ID}`), NOW, pending({ status: "완료", updatedAt: NOW }), pending()],
  ["완료: 수정 시각을 지어냄", "DENY", "update", doc(`guestbook/${ID}`), NOW, pending({ status: "교육완료", updatedAt: YESTERDAY }), pending()],
  ["완료: 등록 시각을 고침", "DENY", "update", doc(`guestbook/${ID}`), NOW, pending({ status: "교육완료", createdAt: NOW, updatedAt: NOW }), pending()],

  ["재등록: 구분·상세 고침", "ALLOW", "update", doc(`guestbook/${ID}`), NOW, pending({ category: "작업", reason: "전기설비 작업", updatedAt: NOW }), pending()],
  ["재등록: 내용 그대로 다시 등록", "ALLOW", "update", doc(`guestbook/${ID}`), NOW, pending({ updatedAt: NOW }), pending()],
  ["재등록: 연락처를 바꿈", "DENY", "update", doc(`guestbook/${ID}`), NOW, pending({ phone: "010-9999-9999", updatedAt: NOW }), pending()],
  ["재등록: 상세 내용을 비움", "DENY", "update", doc(`guestbook/${ID}`), NOW, pending({ reason: "", updatedAt: NOW }), pending()],

  ["조회: 한 건", "DENY", "get", doc(`guestbook/${ID}`), NOW, null, pending()],
  ["조회: 목록", "DENY", "list", doc(`guestbook/${ID}`), NOW, null, pending()],
  ["삭제", "DENY", "delete", doc(`guestbook/${ID}`), NOW, null, pending()],

  ["[기존 유지] morning_brief 조회", "DENY", "get", doc("morning_brief/latest"), NOW, null, { x: 1 }],
  ["[기존 유지] 다른 컬렉션 쓰기", "DENY", "create", doc("anything/x"), NOW, { x: 1 }, null],
];

(async () => {
  let token;
  try {
    token = execSync("gcloud.cmd auth print-access-token", { encoding: "utf8" }).trim();
  } catch (e) {
    console.error("gcloud 액세스 토큰을 못 가져왔습니다. `gcloud auth login` 후 다시 실행하세요.");
    process.exit(2);
  }
  const content = fs.readFileSync(process.env.RULES_FILE || path.join(__dirname, "firestore.rules"), "utf8");
  const testCases = cases.map(([, expectation, method, p, time, newData, oldData]) => ({
    expectation,
    request: { auth: null, path: p, method, time, ...(newData ? { resource: { data: newData } } : {}) },
    ...(oldData ? { resource: { data: oldData } } : {}),
  }));

  const res = await fetch(`https://firebaserules.googleapis.com/v1/projects/${PROJECT}:test`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}`, "x-goog-user-project": PROJECT, "Content-Type": "application/json" },
    body: JSON.stringify({ source: { files: [{ name: "firestore.rules", content }] }, testSuite: { testCases } }),
  });
  const body = await res.json();
  if (!res.ok) { console.error("Rules API 오류:", JSON.stringify(body).slice(0, 800)); process.exit(2); }
  if (body.issues && body.issues.length) { console.error("규칙 컴파일 이슈:", JSON.stringify(body.issues, null, 1)); process.exit(2); }

  let failed = 0;
  (body.testResults || []).forEach((r, i) => {
    const ok = r.state === "SUCCESS";
    if (!ok) failed++;
    const why = ok ? "" : `  ← ${(r.debugMessages || []).join(" / ").slice(0, 200)}`;
    console.log(`  ${ok ? "PASS" : "FAIL"}  ${cases[i][0]}  (기대 ${cases[i][1]})${why}`);
  });
  console.log(failed === 0 ? `\n전체 통과 (${cases.length}건)` : `\n실패 ${failed}건 / ${cases.length}건`);
  process.exit(failed === 0 ? 0 : 1);
})();
