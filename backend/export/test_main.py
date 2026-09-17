"""내보내기 함수 검사 (Firestore 없이): python -m pytest backend/export -q"""
import csv
import io
from datetime import datetime, timezone

import flask
import pytest

import main


def ts(s):
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


DOCS = [
    {"createdAt": ts("2026-09-16T14:59:00"), "company": "맥서브", "name": "가", "phone": "010-1111-1111",
     "address": "울산 남구", "category": "방문", "reason": "미팅", "status": "방문확인"},   # KST 09-16 23:59
    {"createdAt": ts("2026-09-16T15:00:00"), "company": "", "name": "나", "phone": "010-2222-2222",
     "address": "", "category": "작업", "reason": "전기, \"분전반\"", "status": "교육미이수"},  # KST 09-17 00:00
    {"name": "시각 없음"},
    {"createdAt": ts("2026-09-17T03:41:00"), "company": "A", "name": "다", "phone": "01033333333",
     "address": "B", "category": "공사", "reason": "통신", "status": "교육완료"},        # KST 09-17 12:41
]


class FakeQuery:
    def order_by(self, field):
        assert field == "createdAt"
        return self

    def stream(self):
        return [type("Snap", (), {"to_dict": lambda self, d=d: dict(d)})() for d in DOCS]


class FakeDb:
    def collection(self, name):
        assert name == "guestbook"
        return FakeQuery()


@pytest.fixture
def call(monkeypatch):
    monkeypatch.setenv("EXPORT_KEY", "s3cret")
    monkeypatch.setattr(main, "_firestore", lambda: FakeDb())
    app = flask.Flask(__name__)

    def _call(path, method="GET", headers=None):
        with app.test_request_context(path, method=method, headers=headers or {}):
            body, status, hdrs = main.export_csv(flask.request)
            return body, status, hdrs
    return _call


def parse(body):
    assert body.startswith("﻿")
    return list(csv.reader(io.StringIO(body[1:])))


def test_rows_in_kst_with_stable_numbers():
    rows = main.build_rows(DOCS)
    assert [r[:3] for r in rows] == [[1, "2026-09-16", "23:59"], [2, "2026-09-17", "00:00"], [3, "2026-09-17", "12:41"]]
    assert rows[0][3:] == ["맥서브", "가", "010-1111-1111", "울산 남구", "방문", "미팅", "방문확인"]


def test_filter_keeps_original_numbers():
    rows = main.build_rows(DOCS, "2026-09-17", "2026-09-17")
    assert [r[0] for r in rows] == [2, 3]


def test_csv_header_and_quoting(call):
    body, status, hdrs = call("/?key=s3cret")
    assert status == 200
    assert hdrs["Content-Type"].startswith("text/csv")
    table = parse(body)
    assert table[0] == main.COLUMNS
    assert table[2][8] == '전기, "분전반"'
    assert len(table) == 4


def test_key_by_header(call):
    assert call("/", headers={"X-Export-Key": "s3cret"})[1] == 200


@pytest.mark.parametrize("path", ["/", "/?key=wrong", "/?key=s3cre", "/?key="])
def test_bad_key(call, path):
    body, status, _ = call(path)
    assert status == 403
    assert "홍" not in body and "010" not in body


def test_no_key_configured(call, monkeypatch):
    monkeypatch.setenv("EXPORT_KEY", "")
    assert call("/?key=")[1] == 403


def test_bad_dates(call):
    assert call("/?key=s3cret&from=2026-9-1")[1] == 400


def test_post_rejected(call):
    assert call("/?key=s3cret", method="POST")[1] == 405


def test_date_filter_via_http(call):
    table = parse(call("/?key=s3cret&from=2026-09-17")[0])
    assert [r[4] for r in table[1:]] == ["나", "다"]
