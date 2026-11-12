"""안전교육 방명록 서버 (morning-80308 Cloud Run 함수 hilove-export)

GET  /                  엑셀 파워 쿼리용 CSV — 기본(Basic) 인증 비밀번호(CSV_PASSWORD, 사용자 이름은 아무거나).
                        예전 키(헤더 X-Export-Key 또는 ?key=)도 EXPORT_KEY가 있는 동안은 통과. ?from=&to= 로 기간 제한(한국 날짜)
GET  /ping              서버 깨우기 (퇴실 페이지가 열릴 때 부름, 204)
POST /checkout          퇴실 — 누구나. {id} 또는 {name, phone}, 선택 {note}. 퇴실 시각은 서버 시각
POST /admin/login       {password} → 12시간 토큰 (5번 틀리면 15분 잠금)
POST /admin/visits      {date} → 그날 방문 기록            ┐
POST /admin/update      {id, fields}                       │ Authorization: Bearer <토큰>
POST /admin/delete      {id}                               │
POST /admin/password    {current, new}                     ┘
POST /tasks/purge       보유 기간(1년) 지난 방명록 파기 — Cloud Scheduler(hilove-purge)만. 그 서비스 계정의 OIDC 토큰 확인

비밀: hilove-csv-password → CSV_PASSWORD (엑셀 공용 비밀번호)
      hilove-session-secret → SESSION_SECRET (관리자 토큰 서명 — CSV 비밀번호·키와 별개)
      hilove-export-key → EXPORT_KEY (예전 ?key= 방식. 없으면 SESSION_SECRET 없을 때만 토큰 서명에 쓰인다)
"""
import base64
import csv
import hashlib
import hmac
import io
import json
import os
import re
from datetime import datetime, timezone

import functions_framework

import logic
from logic import ApiError
from store import FirestoreStore  # 시작할 때 라이브러리를 읽어 두어 첫 요청을 줄인다(연결은 첫 사용 때)

_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
ALLOWED_ORIGINS = [o.strip() for o in os.environ.get(
    "ALLOWED_ORIGINS", "https://jawon0804-bot.github.io").split(",") if o.strip()]

# /tasks/purge 를 부를 수 있는 호출자: Cloud Scheduler가 이 서비스 계정으로 서명한 OIDC 토큰(대상 = 이 서비스 주소)
PURGE_CALLER = os.environ.get("PURGE_CALLER", "hilove-export@morning-80308.iam.gserviceaccount.com")
PURGE_AUDIENCE = os.environ.get("PURGE_AUDIENCE", "https://hilove-export-n45f6cr3iq-du.a.run.app")

_store = None


def _get_store():
    global _store
    if _store is None:
        _store = FirestoreStore()
    return _store


def _now():
    return datetime.now(timezone.utc)


def _export_key():
    return os.environ.get("EXPORT_KEY", "").strip()


def _csv_password():
    return os.environ.get("CSV_PASSWORD", "").strip()


def _session_secret():
    # 관리자 토큰 서명 비밀. 엑셀에 들어가는 CSV 키·비밀번호와 따로 둔다(SESSION_SECRET).
    # 아직 SESSION_SECRET이 없으면 예전처럼 EXPORT_KEY에서 파생(전환 기간용 — 그동안은 키가 새면 토큰도 위조 가능).
    key = os.environ.get("SESSION_SECRET", "").strip() or _export_key()
    if not key:
        raise ApiError(503, "서버 설정이 없습니다")
    return hmac.new(key.encode(), b"hilove-admin-session", hashlib.sha256).digest()


def to_csv(rows):
    buf = io.StringIO()
    buf.write("﻿")  # 엑셀이 한글을 UTF-8로 읽게
    writer = csv.writer(buf, lineterminator="\r\n")
    writer.writerow(logic.COLUMNS)
    writer.writerows(rows)
    return buf.getvalue()


def _same(given, expected):
    return bool(expected) and hmac.compare_digest(given.encode(), expected.encode())


def _basic_password(request):
    """Authorization: Basic 의 비밀번호(사용자 이름은 무시). 헤더가 없으면 None."""
    auth = request.headers.get("Authorization", "")
    if not auth.lower().startswith("basic "):
        return None
    try:
        decoded = base64.b64decode(auth[6:].strip(), validate=True).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return ""
    return decoded.partition(":")[2]


def _csv_access(request, now):
    """('ok'|'none'|'bad'|'locked', 남은 분). 'none'=자격 증명을 안 보냄(엑셀의 첫 익명 요청 포함)."""
    key = (request.headers.get("X-Export-Key") or request.args.get("key") or "").strip()
    password = _basic_password(request)
    if not key and password is None:
        return "none", 0
    if key and _same(key, _export_key()):
        return "ok", 0           # 예전 방식(?key=) — 전환 기간 동안만. EXPORT_KEY를 비우면 막힌다
    if password is not None:
        store, ip = _get_store(), _client_ip(request)
        wait = logic.csv_locked_minutes(store, ip, now)
        if wait:
            return "locked", wait
        ok = _same(password, _csv_password())
        logic.csv_record(store, ip, ok, now)
        if ok:
            return "ok", 0
    return "bad", 0


def _export(request):
    headers = {"Cache-Control": "no-store"}
    if request.method != "GET":
        return ("GET만 받습니다", 405, headers)
    state, wait = _csv_access(request, _now())
    if state == "none":
        headers["WWW-Authenticate"] = 'Basic realm="hilove", charset="UTF-8"'
        return ("비밀번호가 필요합니다", 401, headers)
    if state == "locked":
        return (f"비밀번호를 여러 번 틀려 잠겼습니다. {wait}분 뒤에 다시 시도해 주세요", 429, headers)
    if state == "bad":
        headers["WWW-Authenticate"] = 'Basic realm="hilove", charset="UTF-8"'
        return ("비밀번호가 틀렸습니다", 401, headers)
    day_from = request.args.get("from") or None
    day_to = request.args.get("to") or None
    for v in (day_from, day_to):
        if v and not _DAY.match(v):
            return ("from/to 는 YYYY-MM-DD 형식입니다", 400, headers)
    rows = logic.build_rows(_get_store().all_visits(), day_from, day_to, _now())
    headers["Content-Type"] = "text/csv; charset=utf-8"
    headers["Content-Disposition"] = 'inline; filename="guestbook.csv"'
    return (to_csv(rows), 200, headers)


def _cors(request):
    origin = request.headers.get("Origin", "")
    h = {"Vary": "Origin", "Cache-Control": "no-store"}
    if origin in ALLOWED_ORIGINS:
        h.update({
            "Access-Control-Allow-Origin": origin,
            "Access-Control-Allow-Methods": "POST, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type, Authorization",
            "Access-Control-Max-Age": "600",
        })
    return h


def _client_ip(request):
    fwd = request.headers.get("X-Forwarded-For", "")
    return fwd.split(",")[0].strip() if fwd else (request.remote_addr or "")


def _api(request, path):
    headers = _cors(request)
    if request.method == "OPTIONS":
        return ("", 204, headers)
    headers["Content-Type"] = "application/json; charset=utf-8"
    try:
        if request.method != "POST":
            raise ApiError(405, "POST만 받습니다")
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            raise ApiError(400, "요청 형식이 올바르지 않습니다")
        store, now = _get_store(), _now()

        if path == "/checkout":
            result = logic.checkout(store, body, now)
        elif path == "/admin/login":
            result = logic.login(store, body, now, _client_ip(request), _session_secret())
        elif path.startswith("/admin/"):
            auth = request.headers.get("Authorization", "")
            token = auth[7:] if auth.startswith("Bearer ") else ""
            cfg = logic.require_admin(store, token, now, _session_secret())
            if path == "/admin/visits":
                result = {**logic.list_visits(store, body, now), "defaultPassword": bool(cfg.get("default"))}
            elif path == "/admin/update":
                result = logic.update_visit(store, body, now)
            elif path == "/admin/delete":
                result = logic.delete_visit(store, body)
            elif path == "/admin/password":
                result = logic.change_password(store, cfg, body, now)
            else:
                raise ApiError(404, "없는 주소입니다")
        else:
            raise ApiError(404, "없는 주소입니다")
        return (json.dumps(result, ensure_ascii=False), 200, headers)
    except ApiError as e:
        return (json.dumps({"ok": False, "error": e.message}, ensure_ascii=False), e.status, headers)


def _verify_id_token(token):
    """Google OIDC 토큰 검증(서명·만료·대상). 실패하면 예외."""
    from google.auth.transport import requests as google_requests
    from google.oauth2 import id_token
    return id_token.verify_oauth2_token(token, google_requests.Request(), audience=PURGE_AUDIENCE)


def _purge(request):
    headers = {"Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store"}

    def reply(status, data):
        return (json.dumps(data, ensure_ascii=False), status, headers)

    if request.method != "POST":
        return reply(405, {"ok": False, "error": "POST만 받습니다"})
    auth = request.headers.get("Authorization", "")
    try:
        claims = _verify_id_token(auth[7:]) if auth.startswith("Bearer ") else None
    except Exception:
        claims = None
    if not claims or claims.get("email") != PURGE_CALLER or claims.get("email_verified") is not True:
        return reply(403, {"ok": False, "error": "권한이 없습니다"})
    result = logic.purge_expired(_get_store(), _now())
    print(json.dumps({"purge": result}, ensure_ascii=False))   # Cloud Logging에 파기 기록을 남긴다
    return reply(200, result)


@functions_framework.http
def export_csv(request):
    path = request.path.rstrip("/") or "/"
    if path == "/":
        return _export(request)
    if path == "/ping":
        return ("", 204, {"Cache-Control": "no-store"})
    if path == "/tasks/purge":
        return _purge(request)
    return _api(request, path)
