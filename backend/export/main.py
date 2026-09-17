"""안전교육 방명록 내보내기 (엑셀 파워 쿼리용 CSV)

morning-80308 Cloud Run 함수. Firestore `guestbook` 컬렉션을 읽어
NO · 날짜 · 시간 · 소속 · 이름 · 연락처 · 주소 · 구분 · 방문내용 · 교육확인 순서의 CSV로 돌려준다.

- 키가 맞아야 열린다: 헤더 `X-Export-Key` 또는 주소의 `?key=`. 값은 Secret Manager `hilove-export-key`.
- `?from=YYYY-MM-DD&to=YYYY-MM-DD` 로 기간을 좁힐 수 있다(한국 날짜, 양끝 포함).
- NO는 전체 기록을 등록 순서로 센 번호라 기간을 좁혀도 바뀌지 않는다.
"""
import csv
import hmac
import io
import os
import re
from datetime import datetime, timedelta, timezone

import functions_framework

KST = timezone(timedelta(hours=9))
COLLECTION = "guestbook"
COLUMNS = ["NO", "날짜", "시간", "소속", "이름", "연락처", "주소", "구분", "방문내용", "교육확인"]
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")

_db = None


def _firestore():
    global _db
    if _db is None:
        from google.cloud import firestore
        _db = firestore.Client()
    return _db


def build_rows(docs, day_from=None, day_to=None):
    """docs: 등록 순서로 정렬된 dict 목록. 날짜는 한국 시간 기준 문자열."""
    rows = []
    no = 0
    for d in docs:
        created = d.get("createdAt")
        if not isinstance(created, datetime):
            continue
        no += 1
        local = created.astimezone(KST)
        day = local.strftime("%Y-%m-%d")
        if (day_from and day < day_from) or (day_to and day > day_to):
            continue
        rows.append([
            no, day, local.strftime("%H:%M"),
            d.get("company", ""), d.get("name", ""), d.get("phone", ""), d.get("address", ""),
            d.get("category", ""), d.get("reason", ""), d.get("status", ""),
        ])
    return rows


def to_csv(rows):
    buf = io.StringIO()
    buf.write("﻿")  # 엑셀이 한글을 UTF-8로 읽게
    writer = csv.writer(buf, lineterminator="\r\n")
    writer.writerow(COLUMNS)
    writer.writerows(rows)
    return buf.getvalue()


def _key_ok(request):
    expected = os.environ.get("EXPORT_KEY", "").strip()
    given = (request.headers.get("X-Export-Key") or request.args.get("key") or "").strip()
    return bool(expected) and hmac.compare_digest(given.encode(), expected.encode())


@functions_framework.http
def export_csv(request):
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

    docs = (s.to_dict() for s in _firestore().collection(COLLECTION).order_by("createdAt").stream())
    body = to_csv(build_rows(docs, day_from, day_to))
    headers["Content-Type"] = "text/csv; charset=utf-8"
    headers["Content-Disposition"] = 'inline; filename="guestbook.csv"'
    return (body, 200, headers)
