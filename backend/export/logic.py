"""방명록 서버 로직 (Firestore·HTTP와 무관한 순수 함수). 저장소는 store 인자로 받는다.

store가 갖춰야 할 것 (store.py의 FirestoreStore, 검사에서는 가짜):
  visits_between(start, end=None) -> [(id, dict)]  등록 시각 순
  get(id) -> dict | None
  update(id, data)        값이 None이면 그 필드를 지운다
  delete(id)
  get_config() -> dict | None / set_config(dict)
  get_lock(key) -> dict | None / set_lock(key, dict) / delete_lock(key)
"""
import hashlib
import hmac
import re
import secrets
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
COLUMNS = ["NO", "날짜", "시간", "소속", "이름", "연락처", "주소", "구분", "방문내용", "교육확인", "전달사항"]
CATEGORIES = ["방문", "점검", "A/S", "작업", "공사"]
STATUSES = ["교육미이수", "교육완료", "방문확인"]
CHECKOUT_WINDOW = timedelta(hours=18)   # 등록 후 이 시간 안에만 스스로 퇴실할 수 있다
NOTE_MAX = 500

ADMIN_ID = "admin"
DEFAULT_PASSWORD = "1234"
PBKDF2_ROUNDS = 200_000
SESSION_HOURS = 12
LOCK_FAILS = 5
LOCK_MINUTES = 15

_ID = re.compile(r"^[A-Za-z0-9]{20}$")
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
_PHONE = re.compile(r"^[0-9-]{9,20}$")


class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


# ── 시간 ──────────────────────────────────────────────

def kst(dt):
    return dt.astimezone(KST)


def day_start(day):
    """'YYYY-MM-DD'(한국 날짜) → 그날 00:00 KST"""
    if not isinstance(day, str) or not _DAY.match(day):
        raise ApiError(400, "날짜는 YYYY-MM-DD 형식입니다")
    y, m, d = map(int, day.split("-"))
    return datetime(y, m, d, tzinfo=KST)


def time_range(created, exit_at, exit_by, now):
    """엑셀 '시간' 칸: 10:00 ~ 16:00 / 10:00 ~ (오늘 미퇴실) / 10:00 ~ 미확인 (지난 날 미퇴실).
    관리자가 넣은 퇴실 시간도 같은 모양으로 쓴다(누가 넣었는지는 exitBy에만 남는다)."""
    start = kst(created)
    text = start.strftime("%H:%M") + " ~"
    if isinstance(exit_at, datetime):
        end = kst(exit_at)
        days = (end.date() - start.date()).days
        text += " " + ("익일 " if days == 1 else (f"+{days}일 " if days > 1 else "")) + end.strftime("%H:%M")
    elif start.date() < kst(now).date():
        text += " 미확인"
    return text


# ── 엑셀 내보내기 ─────────────────────────────────────

def build_rows(docs, day_from=None, day_to=None, now=None):
    """docs: 등록 순서로 정렬된 dict. NO는 전체 기준 번호라 기간을 좁혀도 같다."""
    now = now or datetime.now(timezone.utc)
    rows = []
    no = 0
    for d in docs:
        created = d.get("createdAt")
        if not isinstance(created, datetime):
            continue
        no += 1
        day = kst(created).strftime("%Y-%m-%d")
        if (day_from and day < day_from) or (day_to and day > day_to):
            continue
        rows.append([
            no, day, time_range(created, d.get("exitAt"), d.get("exitBy"), now),
            d.get("company", ""), d.get("name", ""), d.get("phone", ""), d.get("address", ""),
            d.get("category", ""), d.get("reason", ""), d.get("status", ""), d.get("note", ""),
        ])
    return rows


# ── 입력 검사 ─────────────────────────────────────────

def _text(body, key, lo, hi, label):
    v = body.get(key)
    if not isinstance(v, str):
        raise ApiError(400, f"{label} 값이 없습니다")
    v = v.strip()
    if not lo <= len(v) <= hi:
        raise ApiError(400, f"{label}: {lo}~{hi}자로 입력해 주세요")
    return v


def _digits(phone):
    return re.sub(r"\D", "", str(phone or ""))


def _summary(vid, d, now):
    created = d.get("createdAt")
    exit_at = d.get("exitAt")
    return {
        "id": vid,
        "name": d.get("name", ""), "company": d.get("company", ""), "phone": d.get("phone", ""),
        "address": d.get("address", ""), "category": d.get("category", ""), "reason": d.get("reason", ""),
        "status": d.get("status", ""), "note": d.get("note", ""),
        "day": kst(created).strftime("%Y-%m-%d") if isinstance(created, datetime) else "",
        "inAt": kst(created).strftime("%H:%M") if isinstance(created, datetime) else "",
        "outAt": kst(exit_at).strftime("%H:%M") if isinstance(exit_at, datetime) else "",
        "exitBy": d.get("exitBy", ""),
        "time": time_range(created, exit_at, d.get("exitBy"), now) if isinstance(created, datetime) else "",
    }


# ── 퇴실 (누구나) ─────────────────────────────────────

def checkout(store, body, now):
    note = body.get("note") or ""
    if not isinstance(note, str) or len(note.strip()) > NOTE_MAX:
        raise ApiError(400, f"전달사항은 {NOTE_MAX}자까지입니다")
    note = note.strip()

    vid, doc = None, None
    if body.get("id"):
        if not isinstance(body["id"], str) or not _ID.match(body["id"]):
            raise ApiError(400, "잘못된 기록 번호입니다")
        found = store.get(body["id"])
        if found and isinstance(found.get("createdAt"), datetime) and now - found["createdAt"] <= CHECKOUT_WINDOW:
            vid, doc = body["id"], found
        else:
            raise ApiError(404, "기록을 찾을 수 없습니다. 이름과 연락처로 찾아 주세요")
    else:
        name = _text(body, "name", 1, 30, "성함")
        digits = _digits(body.get("phone"))
        if len(digits) < 9:
            raise ApiError(400, "연락처를 정확히 입력해 주세요")
        mine = [(i, d) for i, d in store.visits_between(now - CHECKOUT_WINDOW)
                if d.get("name", "").strip() == name and _digits(d.get("phone")) == digits]
        if not mine:
            raise ApiError(404, "오늘 등록한 기록이 없습니다. 먼저 안전수칙 페이지에서 등록해 주세요")
        open_ = [m for m in mine if not m[1].get("exitAt")]
        vid, doc = (open_ or mine)[-1]

    already = bool(doc.get("exitAt"))
    update = {"updatedAt": now}
    if not already:
        update["exitAt"] = now
        update["exitBy"] = "self"
    if note:
        update["note"] = (doc["note"] + "\n" + note) if doc.get("note") else note
    store.update(vid, update)
    doc = {**doc, **update}
    return {"ok": True, "already": already, **{k: v for k, v in _summary(vid, doc, now).items() if k in
            ("id", "name", "company", "category", "inAt", "outAt")}}


# ── 관리자 인증 ───────────────────────────────────────

def _hash(password, salt, rounds=PBKDF2_ROUNDS):
    return hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), rounds).hex()


def _config(store):
    return store.get_config() or {"version": 0, "default": True}


def _password_ok(cfg, password):
    if cfg.get("default"):
        return hmac.compare_digest(password.encode(), DEFAULT_PASSWORD.encode())
    return hmac.compare_digest(_hash(password, cfg["salt"], cfg.get("rounds", PBKDF2_ROUNDS)), cfg["hash"])


def _sign(secret, payload):
    return hmac.new(secret, payload.encode(), hashlib.sha256).hexdigest()


def _lock_key(ip):
    return hashlib.sha256(("hilove:" + (ip or "")).encode()).hexdigest()[:32]


def login(store, body, now, ip, secret):
    key = _lock_key(ip)
    lock = store.get_lock(key) or {}
    until = lock.get("until")
    if isinstance(until, datetime) and until > now:
        mins = int((until - now).total_seconds() // 60) + 1
        raise ApiError(429, f"비밀번호를 여러 번 틀려 잠겼습니다. {mins}분 뒤에 다시 시도해 주세요")

    password = body.get("password")
    cfg = _config(store)
    if not isinstance(password, str) or not _password_ok(cfg, password):
        first = lock.get("first")
        fails = lock.get("fails", 0)
        if not isinstance(first, datetime) or now - first > timedelta(minutes=LOCK_MINUTES):
            first, fails = now, 0
        fails += 1
        new = {"first": first, "fails": fails}
        if fails >= LOCK_FAILS:
            new["until"] = now + timedelta(minutes=LOCK_MINUTES)
        store.set_lock(key, new)
        left = LOCK_FAILS - fails
        raise ApiError(401, "비밀번호가 틀렸습니다" + (f" (남은 기회 {left}번)" if left > 0 else f". {LOCK_MINUTES}분 동안 잠깁니다"))

    if lock:
        store.delete_lock(key)
    exp = int((now + timedelta(hours=SESSION_HOURS)).timestamp())
    payload = f"{ADMIN_ID}.{exp}.{cfg.get('version', 0)}"
    return {"ok": True, "token": f"{payload}.{_sign(secret, payload)}", "defaultPassword": bool(cfg.get("default"))}


def require_admin(store, token, now, secret):
    parts = (token or "").split(".")
    if len(parts) != 4 or parts[0] != ADMIN_ID:
        raise ApiError(401, "다시 로그인해 주세요")
    payload = ".".join(parts[:3])
    if not hmac.compare_digest(_sign(secret, payload), parts[3]):
        raise ApiError(401, "다시 로그인해 주세요")
    try:
        exp, version = int(parts[1]), int(parts[2])
    except ValueError:
        raise ApiError(401, "다시 로그인해 주세요")
    cfg = _config(store)
    if exp < now.timestamp() or version != cfg.get("version", 0):
        raise ApiError(401, "로그인이 만료되었습니다. 다시 로그인해 주세요")
    return cfg


def change_password(store, cfg, body, now):
    current, new = body.get("current"), body.get("new")
    if not isinstance(current, str) or not _password_ok(cfg, current):
        raise ApiError(400, "현재 비밀번호가 틀렸습니다")
    if not isinstance(new, str) or not 4 <= len(new) <= 64:
        raise ApiError(400, "새 비밀번호는 4~64자로 입력해 주세요")
    if new == current:
        raise ApiError(400, "현재 비밀번호와 다르게 입력해 주세요")
    salt = secrets.token_hex(16)
    store.set_config({"salt": salt, "hash": _hash(new, salt), "rounds": PBKDF2_ROUNDS,
                      "version": cfg.get("version", 0) + 1, "default": False, "updatedAt": now})
    return {"ok": True}   # 버전이 올라가 기존 로그인은 모두 풀린다


# ── 관리자 기능 ───────────────────────────────────────

def list_visits(store, body, now):
    day = body.get("date") or kst(now).strftime("%Y-%m-%d")
    start = day_start(day)
    visits = store.visits_between(start, start + timedelta(days=1))
    return {"ok": True, "date": day, "visits": [_summary(i, d, now) for i, d in visits]}


def update_visit(store, body, now):
    vid = body.get("id")
    if not isinstance(vid, str) or not _ID.match(vid):
        raise ApiError(400, "잘못된 기록 번호입니다")
    doc = store.get(vid)
    if not doc:
        raise ApiError(404, "기록이 없습니다 (삭제되었을 수 있습니다)")
    f = body.get("fields")
    if not isinstance(f, dict) or not f:
        raise ApiError(400, "바꿀 내용이 없습니다")

    update = {}
    limits = {"company": (0, 50, "소속"), "name": (1, 30, "성함"), "address": (0, 100, "주소"),
              "reason": (1, 100, "상세 내용"), "note": (0, NOTE_MAX, "전달사항")}
    for key, value in f.items():
        if key in limits:
            lo, hi, label = limits[key]
            update[key] = _text(f, key, lo, hi, label)
        elif key == "phone":
            phone = f["phone"].strip() if isinstance(f["phone"], str) else ""
            if not _PHONE.match(phone) or len(_digits(phone)) < 9:
                raise ApiError(400, "연락처를 정확히 입력해 주세요")
            update["phone"] = phone
        elif key == "category":
            if value not in CATEGORIES:
                raise ApiError(400, "구분 값이 올바르지 않습니다")
            update["category"] = value
        elif key == "status":
            if value not in STATUSES:
                raise ApiError(400, "교육확인 값이 올바르지 않습니다")
            update["status"] = value
        elif key == "exit":
            if value in ("", None):
                update["exitAt"] = None
                update["exitBy"] = None
            else:
                if not isinstance(value, str) or not _HHMM.match(value):
                    raise ApiError(400, "퇴실 시간은 HH:MM 형식입니다")
                start = kst(doc["createdAt"])
                h, m = map(int, value.split(":"))
                end = start.replace(hour=h, minute=m, second=0, microsecond=0)
                if end < start.replace(second=0, microsecond=0):
                    end += timedelta(days=1)   # 입실보다 이르면 다음 날로 본다
                update["exitAt"] = end.astimezone(timezone.utc)
                update["exitBy"] = "admin"
        else:
            raise ApiError(400, f"바꿀 수 없는 항목입니다: {key}")
    update["updatedAt"] = now
    store.update(vid, update)
    merged = {k: v for k, v in {**doc, **update}.items() if v is not None}
    return {"ok": True, "visit": _summary(vid, merged, now)}


def delete_visit(store, body):
    vid = body.get("id")
    if not isinstance(vid, str) or not _ID.match(vid):
        raise ApiError(400, "잘못된 기록 번호입니다")
    if not store.get(vid):
        raise ApiError(404, "기록이 없습니다")
    store.delete(vid)
    return {"ok": True}
