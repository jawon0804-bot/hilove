"""안전교육 방명록 서버 (morning-80308 Cloud Run 함수 hilove-export)

GET  /                  엑셀 파워 쿼리용 CSV — 키 필요(헤더 X-Export-Key 또는 ?key=). ?from=&to= 로 기간 제한(한국 날짜)
GET  /ping              서버 깨우기 (퇴실 페이지가 열릴 때 부름, 204)
POST /checkout          퇴실 — 누구나. {id} 또는 {name, phone}, 선택 {note}. 퇴실 시각은 서버 시각
POST /admin/login       {password} → 12시간 토큰 (5번 틀리면 15분 잠금)
POST /admin/visits      {date} → 그날 방문 기록            ┐
POST /admin/update      {id, fields}                       │ Authorization: Bearer <토큰>
POST /admin/delete      {id}                               │
POST /admin/password    {current, new}                     ┘

키: Secret Manager hilove-export-key → EXPORT_KEY (관리자 토큰 서명에도 쓴다)
"""
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


def _session_secret():
    key = _export_key()
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


def _key_ok(request):
    expected = _export_key()
    given = (request.headers.get("X-Export-Key") or request.args.get("key") or "").strip()
    return bool(expected) and hmac.compare_digest(given.encode(), expected.encode())


def _export(request):
    headers = {"Cache-Control": "no-store"}
    if request.method != "GET":
        return ("GET만 받습니다", 405, headers)
    if not _key_ok(request):
        return ("키가 없거나 틀렸습니다", 403, headers)
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


@functions_framework.http
def export_csv(request):
    path = request.path.rstrip("/") or "/"
    if path == "/":
        return _export(request)
    if path == "/ping":
        return ("", 204, {"Cache-Control": "no-store"})
    return _api(request, path)
