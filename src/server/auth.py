"""アクセスキーの発行・保存・検証。

平文キーは保存しない。keys.json には SHA-256 ハッシュだけを置く。
"""

from __future__ import annotations

import hashlib
import json
import secrets
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

KEY_PREFIX = "cw"


def generate_key() -> str:
    """新しい平文キーを作る。返した後は保存されないので呼び出し側が控える。"""
    return f"{KEY_PREFIX}_{secrets.token_urlsafe(32)}"


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


@dataclass
class KeyRecord:
    id: str
    name: str
    hash: str
    created_at: str
    revoked: bool = False


class KeyStore:
    """keys.json を読み書きするだけの素朴なストア。"""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.records: list[KeyRecord] = []
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            self.records = []
            return
        data = json.loads(self.path.read_text())
        self.records = [KeyRecord(**r) for r in data.get("keys", [])]

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"keys": [asdict(r) for r in self.records]}
        self.path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
        self.path.chmod(0o600)

    def issue(self, name: str) -> tuple[str, KeyRecord]:
        """キーを発行して保存し、(平文キー, 記録) を返す。"""
        key = generate_key()
        record = KeyRecord(
            id=secrets.token_hex(4),
            name=name,
            hash=hash_key(key),
            created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )
        self.records.append(record)
        self.save()
        return key, record

    def revoke(self, key_id: str) -> bool:
        for r in self.records:
            if r.id == key_id:
                r.revoked = True
                self.save()
                return True
        return False

    def verify(self, key: str) -> KeyRecord | None:
        """平文キーに対応する有効な記録を返す。無ければ None。"""
        digest = hash_key(key)
        for r in self.records:
            if not r.revoked and secrets.compare_digest(r.hash, digest):
                return r
        return None
