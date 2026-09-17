"""Firestore 저장소 (서버 권한). 컬렉션:
  guestbook                방명록 (페이지가 규칙 안에서 직접 쓰고, 퇴실·관리자 수정은 여기서)
  hilove_admin/config      관리자 비밀번호 해시·버전 (브라우저 접근 불가 — 규칙의 전체 차단에 걸림)
  hilove_admin_locks/{키}  비밀번호 오입력 잠금 (IP 해시 기준)
"""
from google.cloud import firestore
from google.cloud.firestore_v1 import FieldFilter

VISITS = "guestbook"


class FirestoreStore:
    def __init__(self):
        self.db = firestore.Client()

    def visits_between(self, start, end=None):
        q = self.db.collection(VISITS).where(filter=FieldFilter("createdAt", ">=", start))
        if end is not None:
            q = q.where(filter=FieldFilter("createdAt", "<", end))
        return [(s.id, s.to_dict()) for s in q.order_by("createdAt").stream()]

    def all_visits(self):
        return [s.to_dict() for s in self.db.collection(VISITS).order_by("createdAt").stream()]

    def get(self, vid):
        snap = self.db.collection(VISITS).document(vid).get()
        return snap.to_dict() if snap.exists else None

    def update(self, vid, data):
        self.db.collection(VISITS).document(vid).update(
            {k: (firestore.DELETE_FIELD if v is None else v) for k, v in data.items()})

    def delete(self, vid):
        self.db.collection(VISITS).document(vid).delete()

    def _config_ref(self):
        return self.db.collection("hilove_admin").document("config")

    def get_config(self):
        snap = self._config_ref().get()
        return snap.to_dict() if snap.exists else None

    def set_config(self, data):
        self._config_ref().set(data)

    def _lock_ref(self, key):
        return self.db.collection("hilove_admin_locks").document(key)

    def get_lock(self, key):
        snap = self._lock_ref(key).get()
        return snap.to_dict() if snap.exists else None

    def set_lock(self, key, data):
        self._lock_ref(key).set(data)

    def delete_lock(self, key):
        self._lock_ref(key).delete()
