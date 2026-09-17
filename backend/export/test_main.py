"""방명록 서버 검사 (Firestore 없이 가짜 저장소): python -m pytest backend/export -q"""
import copy
import csv
import io
import json
from datetime import datetime, timedelta, timezone

import flask
import pytest

import logic
import main

UTC = timezone.utc
KST = logic.KST
NOW = datetime(2026, 9, 17, 7, 0, tzinfo=UTC)          # 16:00 KST
ORIGIN = "https://jawon0804-bot.github.io"


def kst(y, mo, d, h, mi):
    return datetime(y, mo, d, h, mi, tzinfo=KST).astimezone(UTC)


def vid(n):
    return f"id{n:018d}"


class FakeStore:
    def __init__(self, visits):
        self.visits = {k: dict(v) for k, v in visits.items()}
        self.config = None
        self.locks = {}

    def visits_between(self, start, end=None):
        out = [(k, copy.deepcopy(v)) for k, v in self.visits.items()
               if isinstance(v.get("createdAt"), datetime) and v["createdAt"] >= start
               and (end is None or v["createdAt"] < end)]
        return sorted(out, key=lambda kv: kv[1]["createdAt"])

    def all_visits(self):
        # 실제 저장소는 createdAt 순 정렬이라 이 필드가 없는 문서는 빠진다. 여기선 섞어 넣어 건너뛰는지 본다
        return [{"name": "시각 없음"}] + [d for _, d in self.visits_between(datetime(2000, 1, 1, tzinfo=UTC))]

    def get(self, k):
        return copy.deepcopy(self.visits.get(k))

    def update(self, k, data):
        assert k in self.visits, "update on missing doc"
        for key, value in data.items():
            if value is None:
                self.visits[k].pop(key, None)
            else:
                self.visits[k][key] = value

    def delete(self, k):
        self.visits.pop(k)

    def get_config(self):
        return copy.deepcopy(self.config)

    def set_config(self, d):
        self.config = dict(d)

    def get_lock(self, key):
        return copy.deepcopy(self.locks.get(key))

    def set_lock(self, key, d):
        self.locks[key] = dict(d)

    def delete_lock(self, key):
        self.locks.pop(key, None)


def person(**over):
    base = {"company": "맥서브", "name": "홍길동", "phone": "010-1111-1111", "address": "울산 남구",
            "category": "작업", "reason": "전기", "status": "교육완료"}
    return {**base, **over}


def seed():
    return {
        vid(1): person(createdAt=kst(2026, 9, 16, 9, 0), name="어제", phone="010-9999-9999"),       # 어제, 퇴실 없음
        vid(2): person(createdAt=kst(2026, 9, 16, 10, 0), name="어제퇴실", exitAt=kst(2026, 9, 16, 15, 30), exitBy="self"),
        vid(3): person(createdAt=kst(2026, 9, 17, 0, 0), name="가", reason='전기, "분전반"'),       # 오늘 00:00
        vid(4): person(createdAt=kst(2026, 9, 17, 10, 0)),                                             # 오늘 홍길동
    }


@pytest.fixture
def store(monkeypatch):
    s = FakeStore(seed())
    monkeypatch.setenv("EXPORT_KEY", "s3cret")
    monkeypatch.setattr(main, "_store", s)
    monkeypatch.setattr(main, "_now", lambda: NOW)
    return s


@pytest.fixture
def http(store):
    app = flask.Flask(__name__)

    def _call(path, method="GET", body=None, headers=None, raw=None):
        h = {"Origin": ORIGIN, **(headers or {})}
        data = raw if raw is not None else (json.dumps(body) if body is not None else None)
        with app.test_request_context(path, method=method, headers=h, data=data,
                                      content_type="application/json" if data is not None else None,
                                      environ_base={"REMOTE_ADDR": "1.2.3.4"}):
            text, status, hdrs = main.export_csv(flask.request)
            return text, status, hdrs
    return _call


def api(http, path, body, token=None, headers=None):
    h = dict(headers or {})
    if token:
        h["Authorization"] = f"Bearer {token}"
    text, status, hdrs = http(path, "POST", body, h)
    return status, json.loads(text), hdrs


def login(http, password="1234"):
    status, res, _ = api(http, "/admin/login", {"password": password})
    assert status == 200, res
    return res["token"]


def parse(text):
    assert text.startswith("﻿")
    return list(csv.reader(io.StringIO(text[1:])))


# ── 엑셀 내보내기 ─────────────────────────────────────

def test_time_range_formats():
    tr = logic.time_range
    c = kst(2026, 9, 17, 10, 0)
    assert tr(c, None, None, NOW) == "10:00 ~"
    assert tr(c, kst(2026, 9, 17, 16, 5), "self", NOW) == "10:00 ~ 16:05"
    assert tr(c, kst(2026, 9, 17, 16, 5), "admin", NOW) == "10:00 ~ 16:05(관리자)"
    assert tr(c, kst(2026, 9, 18, 1, 0), "self", NOW) == "10:00 ~ 익일 01:00"
    assert tr(kst(2026, 9, 16, 23, 0), None, None, NOW) == "23:00 ~ 미확인"


def test_csv(http):
    text, status, hdrs = http("/?key=s3cret")
    assert status == 200 and hdrs["Content-Type"].startswith("text/csv")
    table = parse(text)
    assert table[0] == logic.COLUMNS and len(table[0]) == 11
    assert [r[:3] for r in table[1:]] == [["1", "2026-09-16", "09:00 ~ 미확인"], ["2", "2026-09-16", "10:00 ~ 15:30"],
                                          ["3", "2026-09-17", "00:00 ~"], ["4", "2026-09-17", "10:00 ~"]]
    assert table[3][8] == '전기, "분전반"'
    assert table[1][3:] == ["맥서브", "어제", "010-9999-9999", "울산 남구", "작업", "전기", "교육완료", ""]


def test_csv_filter_keeps_numbers(http):
    table = parse(http("/?key=s3cret&from=2026-09-17&to=2026-09-17")[0])
    assert [r[0] for r in table[1:]] == ["3", "4"]


def test_csv_key_by_header(http):
    assert http("/", headers={"X-Export-Key": "s3cret"})[1] == 200


@pytest.mark.parametrize("path", ["/", "/?key=wrong", "/?key=s3cre", "/?key="])
def test_csv_bad_key(http, path):
    text, status, _ = http(path)
    assert status == 403 and "010" not in text


def test_csv_no_key_configured(http, monkeypatch):
    monkeypatch.setenv("EXPORT_KEY", "")
    assert http("/?key=")[1] == 403


def test_csv_bad_dates_and_method(http):
    assert http("/?key=s3cret&from=2026-9-1")[1] == 400
    assert http("/?key=s3cret", method="POST", body={})[1] == 405


# ── 퇴실 ──────────────────────────────────────────────

def test_checkout_by_id_with_note(http, store):
    status, res, hdrs = api(http, "/checkout", {"id": vid(4), "note": "  분전반 복구 완료 "})
    assert status == 200 and res["outAt"] == "16:00" and res["already"] is False and res["name"] == "홍길동"
    assert hdrs["Access-Control-Allow-Origin"] == ORIGIN
    v = store.visits[vid(4)]
    assert v["exitAt"] == NOW and v["exitBy"] == "self" and v["note"] == "분전반 복구 완료"
    assert "phone" not in res   # 연락처는 돌려주지 않는다


def test_checkout_again_keeps_time_and_appends_note(http, store):
    api(http, "/checkout", {"id": vid(4), "note": "첫째"})
    store.visits[vid(4)]["exitAt"] = kst(2026, 9, 17, 15, 0)
    status, res, _ = api(http, "/checkout", {"id": vid(4), "note": "둘째"})
    assert res["already"] is True and res["outAt"] == "15:00"
    assert store.visits[vid(4)]["note"] == "첫째\n둘째"


def test_checkout_by_name_phone_ignores_hyphens(http, store):
    status, res, _ = api(http, "/checkout", {"name": " 홍길동 ", "phone": "01011111111"})
    assert status == 200 and res["id"] == vid(4)


def test_checkout_by_name_prefers_open_record(http, store):
    store.visits[vid(5)] = person(createdAt=kst(2026, 9, 17, 13, 0), exitAt=kst(2026, 9, 17, 14, 0), exitBy="self")
    status, res, _ = api(http, "/checkout", {"name": "홍길동", "phone": "010-1111-1111"})
    assert res["id"] == vid(4) and res["already"] is False


def test_checkout_old_record_rejected(http, store):
    # 어제 09:00 등록 → 18시간 넘음
    status, res, _ = api(http, "/checkout", {"id": vid(1)})
    assert status == 404
    status, res, _ = api(http, "/checkout", {"name": "어제", "phone": "010-9999-9999"})
    assert status == 404 and "등록" in res["error"]
    assert "exitAt" not in store.visits[vid(1)]


@pytest.mark.parametrize("body,code", [
    ({"id": "short"}, 400),
    ({"id": "x" * 20}, 404),
    ({"name": "", "phone": "01011111111"}, 400),
    ({"name": "홍길동", "phone": "0101"}, 400),
    ({"name": "홍길동", "phone": "010-2222-2222"}, 404),
    ({"id": vid(4), "note": "가" * 501}, 400),
    ({"id": vid(4), "note": 5}, 400),
])
def test_checkout_bad_input(http, store, body, code):
    before = copy.deepcopy(store.visits)
    assert api(http, "/checkout", body)[0] == code
    assert store.visits == before


def test_preflight_and_origins(http):
    _, status, hdrs = http("/checkout", "OPTIONS")
    assert status == 204 and hdrs["Access-Control-Allow-Origin"] == ORIGIN
    assert "Authorization" in hdrs["Access-Control-Allow-Headers"]
    _, status, hdrs = http("/checkout", "OPTIONS", headers={"Origin": "https://evil.example"})
    assert "Access-Control-Allow-Origin" not in hdrs


def test_ping(http, store):
    body, status, _ = http("/ping")
    assert status == 204 and body == ""


def test_api_rejects_get_and_bad_json(http):
    assert http("/checkout", "GET")[1] == 405
    assert http("/checkout", "POST", raw="not json")[1] == 400
    assert api(http, "/nope", {})[0] == 404


# ── 관리자 로그인 ─────────────────────────────────────

def test_default_password_login(http):
    status, res, _ = api(http, "/admin/login", {"password": "1234"})
    assert status == 200 and res["defaultPassword"] is True and res["token"].startswith("admin.")


def test_lockout_after_five_failures(http, store):
    for i in range(4):
        status, res, _ = api(http, "/admin/login", {"password": "0000"})
        assert status == 401 and f"남은 기회 {4 - i}번" in res["error"]
    status, res, _ = api(http, "/admin/login", {"password": "0000"})
    assert status == 401 and "잠깁니다" in res["error"]
    status, res, _ = api(http, "/admin/login", {"password": "1234"})
    assert status == 429          # 맞는 비밀번호도 잠금 중엔 거부
    main._now = lambda: NOW + timedelta(minutes=16)   # store 픽스처의 monkeypatch가 끝나면 되돌린다
    assert api(http, "/admin/login", {"password": "1234"})[0] == 200
    assert store.locks == {}      # 성공하면 잠금 기록 삭제


def test_failures_reset_after_window(http, store, monkeypatch):
    for _ in range(4):
        api(http, "/admin/login", {"password": "0000"})
    monkeypatch.setattr(main, "_now", lambda: NOW + timedelta(minutes=20))
    status, res, _ = api(http, "/admin/login", {"password": "0000"})
    assert "남은 기회 4번" in res["error"]


def test_lock_is_per_ip(http, store):
    for _ in range(5):
        api(http, "/admin/login", {"password": "0000"}, headers={"X-Forwarded-For": "9.9.9.9, 10.0.0.1"})
    assert api(http, "/admin/login", {"password": "1234"}, headers={"X-Forwarded-For": "9.9.9.9"})[0] == 429
    assert api(http, "/admin/login", {"password": "1234"}, headers={"X-Forwarded-For": "8.8.8.8"})[0] == 200


@pytest.mark.parametrize("token", ["", "admin.1.0", "admin.9999999999.0.bad", "root.9999999999.0.x"])
def test_admin_requires_valid_token(http, token):
    assert api(http, "/admin/visits", {}, token=token)[0] == 401


def test_token_expires(http, monkeypatch):
    token = login(http)
    monkeypatch.setattr(main, "_now", lambda: NOW + timedelta(hours=12, minutes=1))
    assert api(http, "/admin/visits", {}, token=token)[0] == 401


def test_token_signed_with_export_key(http, monkeypatch):
    token = login(http)
    monkeypatch.setenv("EXPORT_KEY", "other")
    assert api(http, "/admin/visits", {}, token=token)[0] == 401


# ── 비밀번호 변경 ─────────────────────────────────────

def test_change_password_flow(http, store):
    token = login(http)
    assert api(http, "/admin/password", {"current": "0000", "new": "abcd"}, token)[0] == 400
    assert api(http, "/admin/password", {"current": "1234", "new": "123"}, token)[0] == 400
    assert api(http, "/admin/password", {"current": "1234", "new": "1234"}, token)[0] == 400
    status, res, _ = api(http, "/admin/password", {"current": "1234", "new": "새비번5678"}, token)
    assert status == 200
    assert "새비번5678" not in json.dumps(store.config, default=str) and store.config["default"] is False
    assert api(http, "/admin/visits", {}, token)[0] == 401          # 기존 로그인 무효
    assert api(http, "/admin/login", {"password": "1234"})[0] == 401
    token2 = login(http, "새비번5678")
    status, res, _ = api(http, "/admin/visits", {}, token2)
    assert status == 200 and res["defaultPassword"] is False


# ── 관리자 조회·수정·삭제 ──────────────────────────────

def test_list_today_default(http):
    status, res, _ = api(http, "/admin/visits", {}, login(http))
    assert res["date"] == "2026-09-17" and [v["id"] for v in res["visits"]] == [vid(3), vid(4)]
    assert res["defaultPassword"] is True
    v = res["visits"][1]
    assert v["phone"] == "010-1111-1111" and v["inAt"] == "10:00" and v["outAt"] == "" and v["time"] == "10:00 ~"


def test_list_other_day(http):
    status, res, _ = api(http, "/admin/visits", {"date": "2026-09-16"}, login(http))
    assert [v["name"] for v in res["visits"]] == ["어제", "어제퇴실"]
    assert res["visits"][0]["time"] == "09:00 ~ 미확인"
    assert api(http, "/admin/visits", {"date": "9/16"}, login(http))[0] == 400


def test_admin_sets_exit_time_and_fields(http, store):
    token = login(http)
    status, res, _ = api(http, "/admin/update", {"id": vid(1), "fields": {
        "exit": "17:30", "note": "전화 확인", "company": " 새소속 ", "category": "점검", "status": "교육미이수"}}, token)
    assert status == 200 and res["visit"]["time"] == "09:00 ~ 17:30(관리자)"
    v = store.visits[vid(1)]
    assert v["exitAt"] == kst(2026, 9, 16, 17, 30) and v["exitBy"] == "admin"
    assert (v["company"], v["category"], v["status"], v["note"]) == ("새소속", "점검", "교육미이수", "전화 확인")
    assert v["updatedAt"] == NOW


def test_admin_exit_earlier_than_entry_is_next_day(http, store):
    api(http, "/admin/update", {"id": vid(4), "fields": {"exit": "02:00"}}, login(http))
    assert store.visits[vid(4)]["exitAt"] == kst(2026, 9, 18, 2, 0)


def test_admin_clears_exit(http, store):
    status, res, _ = api(http, "/admin/update", {"id": vid(2), "fields": {"exit": ""}}, login(http))
    assert "exitAt" not in store.visits[vid(2)] and "exitBy" not in store.visits[vid(2)]
    assert res["visit"]["time"] == "10:00 ~ 미확인"


@pytest.mark.parametrize("fields", [
    {}, {"exit": "25:00"}, {"exit": "9:00"}, {"category": "기타"}, {"status": "완료"},
    {"name": ""}, {"phone": "01011"}, {"phone": "010-abcd-1111"}, {"reason": ""},
    {"note": "가" * 501}, {"createdAt": "x"}, {"exitBy": "self"},
])
def test_admin_update_rejects(http, store, fields):
    before = copy.deepcopy(store.visits)
    assert api(http, "/admin/update", {"id": vid(4), "fields": fields}, login(http))[0] == 400
    assert store.visits == before


def test_admin_update_missing_doc(http):
    assert api(http, "/admin/update", {"id": "z" * 20, "fields": {"exit": "10:00"}}, login(http))[0] == 404


def test_admin_delete(http, store):
    token = login(http)
    assert api(http, "/admin/delete", {"id": vid(3)}, token)[0] == 200
    assert vid(3) not in store.visits
    assert api(http, "/admin/delete", {"id": vid(3)}, token)[0] == 404
    assert api(http, "/admin/delete", {"id": "bad"}, token)[0] == 400


def test_admin_actions_need_login(http, store):
    before = copy.deepcopy(store.visits)
    assert api(http, "/admin/update", {"id": vid(4), "fields": {"exit": "10:00"}})[0] == 401
    assert api(http, "/admin/delete", {"id": vid(4)})[0] == 401
    assert api(http, "/admin/password", {"current": "1234", "new": "abcd"})[0] == 401
    assert store.visits == before and store.config is None
