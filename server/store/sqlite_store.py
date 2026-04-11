"""SQLite implementation of ``AppStore`` (users, sessions, KBs, members)."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from server.passwords import verify_password
from server.store.protocol import KBPermission, KBRecord, UserRecord


def _connect_rw(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn


_INIT_SQL = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL COLLATE NOCASE UNIQUE,
    password_hash TEXT NOT NULL,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    token TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS knowledge_bases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL COLLATE NOCASE UNIQUE,
    description TEXT NOT NULL DEFAULT '',
    readme_md TEXT NOT NULL DEFAULT '',
    db_path TEXT NOT NULL,
    owner_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at REAL NOT NULL,
    icon TEXT NOT NULL DEFAULT 'book',
    kb_ready INTEGER NOT NULL DEFAULT 1,
    source_type TEXT NOT NULL DEFAULT 'tar',
    webhook_provider TEXT NOT NULL DEFAULT '',
    webhook_secret TEXT NOT NULL DEFAULT '',
    webhook_repo_url TEXT NOT NULL DEFAULT '',
    webhook_ref TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS kb_members (
    kb_id INTEGER NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    can_read INTEGER NOT NULL DEFAULT 1,
    can_write INTEGER NOT NULL DEFAULT 0,
    can_delete INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (kb_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_sessions_expires ON sessions(expires_at);

CREATE TABLE IF NOT EXISTS kb_subscriptions (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kb_id INTEGER NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,
    created_at REAL NOT NULL,
    PRIMARY KEY (user_id, kb_id)
);
CREATE INDEX IF NOT EXISTS idx_kb_sub_kb ON kb_subscriptions(kb_id);

CREATE TABLE IF NOT EXISTS api_keys (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name TEXT NOT NULL DEFAULT '',
    key_value TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_api_keys_user ON api_keys(user_id);
"""


class SqliteAppStore:
    def __init__(self, db_path: Path) -> None:
        self._path = db_path
        self._lock = threading.Lock()
        self._conn = _connect_rw(db_path)
        self._conn.executescript(_INIT_SQL)
        self._migrate_schema_unlocked()
        self._conn.commit()

    def _migrate_schema_unlocked(self) -> None:
        cols = {str(r[1]) for r in self._conn.execute("PRAGMA table_info(users)").fetchall()}
        if "avatar_mime" not in cols:
            self._conn.execute("ALTER TABLE users ADD COLUMN avatar_mime TEXT")
        if "gitlab_pat" not in cols:
            self._conn.execute("ALTER TABLE users ADD COLUMN gitlab_pat TEXT NOT NULL DEFAULT ''")
        if "github_pat" not in cols:
            self._conn.execute("ALTER TABLE users ADD COLUMN github_pat TEXT NOT NULL DEFAULT ''")
        kb_cols = {str(r[1]) for r in self._conn.execute("PRAGMA table_info(knowledge_bases)").fetchall()}
        if "list_color_idx" not in kb_cols:
            self._conn.execute(
                "ALTER TABLE knowledge_bases ADD COLUMN list_color_idx INTEGER NOT NULL DEFAULT 0"
            )
        if "is_public" not in kb_cols:
            self._conn.execute(
                "ALTER TABLE knowledge_bases ADD COLUMN is_public INTEGER NOT NULL DEFAULT 0"
            )
        if "readme_md" not in kb_cols:
            self._conn.execute(
                "ALTER TABLE knowledge_bases ADD COLUMN readme_md TEXT NOT NULL DEFAULT ''"
            )
        if "icon" not in kb_cols:
            self._conn.execute(
                "ALTER TABLE knowledge_bases ADD COLUMN icon TEXT NOT NULL DEFAULT 'book'"
            )
        if "source_type" not in kb_cols:
            self._conn.execute(
                "ALTER TABLE knowledge_bases ADD COLUMN source_type TEXT NOT NULL DEFAULT 'tar'"
            )
        if "webhook_provider" not in kb_cols:
            self._conn.execute(
                "ALTER TABLE knowledge_bases ADD COLUMN webhook_provider TEXT NOT NULL DEFAULT ''"
            )
        if "webhook_secret" not in kb_cols:
            self._conn.execute(
                "ALTER TABLE knowledge_bases ADD COLUMN webhook_secret TEXT NOT NULL DEFAULT ''"
            )
        if "webhook_repo_url" not in kb_cols:
            self._conn.execute(
                "ALTER TABLE knowledge_bases ADD COLUMN webhook_repo_url TEXT NOT NULL DEFAULT ''"
            )
        if "webhook_ref" not in kb_cols:
            self._conn.execute(
                "ALTER TABLE knowledge_bases ADD COLUMN webhook_ref TEXT NOT NULL DEFAULT ''"
            )
        self._conn.execute("UPDATE kb_members SET can_read = 1, can_delete = 0")
        subs = self._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='kb_subscriptions'"
        ).fetchone()
        if subs is None:
            self._conn.executescript(
                """
                CREATE TABLE kb_subscriptions (
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    kb_id INTEGER NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,
                    created_at REAL NOT NULL,
                    PRIMARY KEY (user_id, kb_id)
                );
                CREATE INDEX IF NOT EXISTS idx_kb_sub_kb ON kb_subscriptions(kb_id);
                """
            )
        api_key_tab = self._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='api_keys'"
        ).fetchone()
        if api_key_tab is None:
            self._conn.executescript(
                """
                CREATE TABLE api_keys (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    name TEXT NOT NULL DEFAULT '',
                    key_value TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_api_keys_user ON api_keys(user_id);
                """
            )
        bj = self._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='build_jobs'"
        ).fetchone()
        if bj is None:
            self._conn.executescript(
                """
                CREATE TABLE build_jobs (
                    job_id TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    task_kind TEXT NOT NULL,
                    op TEXT NOT NULL,
                    kb_name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    cancel_requested INTEGER NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL,
                    started_at REAL,
                    finished_at REAL,
                    percent INTEGER NOT NULL DEFAULT 0,
                    phase TEXT NOT NULL DEFAULT 'queued',
                    detail TEXT NOT NULL DEFAULT '',
                    error TEXT,
                    result_json TEXT,
                    upload_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE INDEX idx_build_jobs_user_created ON build_jobs(user_id, created_at DESC);
                CREATE INDEX idx_build_jobs_status_created ON build_jobs(status, created_at ASC);
                """
            )
        if "kb_ready" not in kb_cols:
            self._conn.execute(
                "ALTER TABLE knowledge_bases ADD COLUMN kb_ready INTEGER NOT NULL DEFAULT 1"
            )
        self._conn.execute(
            "UPDATE build_jobs SET status = 'queued', started_at = NULL "
            "WHERE status = 'running'"
        )
        self._conn.execute(
            "DELETE FROM build_jobs WHERE status IN ('done', 'error', 'cancelled')"
        )

    def _kb_ready_from_row(self, kb: sqlite3.Row) -> bool:
        try:
            return int(kb["kb_ready"] or 0) == 1
        except (KeyError, IndexError, TypeError, ValueError):
            return True

    def _avatar_dir(self) -> Path:
        d = self._path.parent / "avatars"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _avatar_file(self, user_id: int) -> Path:
        return self._avatar_dir() / str(int(user_id))

    def _kb_icon_dir(self) -> Path:
        d = self._path.parent / "kb_icons"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _kb_icon_file(self, kb_id: int) -> Path:
        return self._kb_icon_dir() / str(int(kb_id))

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _now(self) -> float:
        return time.time()

    def _purge_expired_sessions_unlocked(self) -> None:
        self._conn.execute("DELETE FROM sessions WHERE expires_at < ?", (self._now(),))

    def create_user(self, username: str, password_hash: str) -> UserRecord:
        t = self._now()
        with self._lock:
            try:
                cur = self._conn.execute(
                    "INSERT INTO users(username, password_hash, created_at) VALUES(?,?,?)",
                    (username.strip(), password_hash, t),
                )
                self._conn.commit()
                uid = int(cur.lastrowid)
            except sqlite3.IntegrityError as e:
                self._conn.rollback()
                raise ValueError("Username already taken") from e
        return UserRecord(id=uid, username=username.strip())

    def get_user_by_username(self, username: str) -> UserRecord | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT id, username FROM users WHERE username = ? COLLATE NOCASE",
                (username.strip(),),
            ).fetchone()
        if row is None:
            return None
        return UserRecord(id=int(row["id"]), username=str(row["username"]))

    def get_user_by_id(self, user_id: int) -> UserRecord | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT id, username FROM users WHERE id = ?",
                (int(user_id),),
            ).fetchone()
        if row is None:
            return None
        return UserRecord(id=int(row["id"]), username=str(row["username"]))

    def verify_user_password(self, username: str, password: str) -> UserRecord | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT id, username, password_hash FROM users WHERE username = ? COLLATE NOCASE",
                (username.strip(),),
            ).fetchone()
        if row is None:
            return None
        if not verify_password(password, str(row["password_hash"] or "")):
            return None
        return UserRecord(id=int(row["id"]), username=str(row["username"]))

    def create_session(self, user_id: int, *, ttl_seconds: int) -> str:
        import secrets

        token = secrets.token_urlsafe(32)
        t = self._now()
        exp = t + float(ttl_seconds)
        with self._lock:
            self._purge_expired_sessions_unlocked()
            self._conn.execute(
                "INSERT INTO sessions(token, user_id, created_at, expires_at) VALUES(?,?,?,?)",
                (token, int(user_id), t, exp),
            )
            self._conn.commit()
        return token

    def get_session_user_id(self, token: str) -> int | None:
        if not token:
            return None
        with self._lock:
            self._purge_expired_sessions_unlocked()
            row = self._conn.execute(
                "SELECT user_id FROM sessions WHERE token = ? AND expires_at >= ?",
                (token, self._now()),
            ).fetchone()
        if row is None:
            return None
        return int(row["user_id"])

    def delete_session(self, token: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
            self._conn.commit()

    def change_password(self, user_id: int, current_password: str, new_password_hash: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT password_hash FROM users WHERE id = ?",
                (int(user_id),),
            ).fetchone()
            if row is None:
                return False
            if not verify_password(current_password, str(row["password_hash"] or "")):
                return False
            self._conn.execute(
                "UPDATE users SET password_hash = ? WHERE id = ?",
                (new_password_hash, int(user_id)),
            )
            self._conn.commit()
        return True

    def _user_has_avatar_unlocked(self, user_id: int) -> bool:
        row = self._conn.execute(
            "SELECT avatar_mime FROM users WHERE id = ?",
            (int(user_id),),
        ).fetchone()
        if row is None or not row["avatar_mime"]:
            return False
        return self._avatar_file(user_id).is_file()

    def user_has_avatar(self, user_id: int) -> bool:
        with self._lock:
            return self._user_has_avatar_unlocked(user_id)

    def save_avatar(self, user_id: int, mime: str, body: bytes) -> None:
        path = self._avatar_file(user_id)
        with self._lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(body)
            self._conn.execute(
                "UPDATE users SET avatar_mime = ? WHERE id = ?",
                (mime, int(user_id)),
            )
            self._conn.commit()

    def load_avatar(self, user_id: int) -> tuple[str, bytes] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT avatar_mime FROM users WHERE id = ?",
                (int(user_id),),
            ).fetchone()
        if row is None or not row["avatar_mime"]:
            return None
        path = self._avatar_file(user_id)
        if not path.is_file():
            return None
        try:
            data = path.read_bytes()
        except OSError:
            return None
        return str(row["avatar_mime"]), data

    def clear_avatar(self, user_id: int) -> None:
        path = self._avatar_file(user_id)
        with self._lock:
            self._conn.execute(
                "UPDATE users SET avatar_mime = NULL WHERE id = ?",
                (int(user_id),),
            )
            self._conn.commit()
        try:
            if path.is_file():
                path.unlink()
        except OSError:
            pass

    def set_user_gitlab_pat(self, user_id: int, pat: str) -> None:
        token = str(pat or "").strip()
        with self._lock:
            self._conn.execute(
                "UPDATE users SET gitlab_pat = ? WHERE id = ?",
                (token, int(user_id)),
            )
            self._conn.commit()

    def get_user_gitlab_pat(self, user_id: int) -> str:
        with self._lock:
            row = self._conn.execute(
                "SELECT gitlab_pat FROM users WHERE id = ?",
                (int(user_id),),
            ).fetchone()
        if row is None:
            return ""
        return str(row["gitlab_pat"] or "").strip()

    def set_user_github_pat(self, user_id: int, pat: str) -> None:
        token = str(pat or "").strip()
        with self._lock:
            self._conn.execute(
                "UPDATE users SET github_pat = ? WHERE id = ?",
                (token, int(user_id)),
            )
            self._conn.commit()

    def get_user_github_pat(self, user_id: int) -> str:
        with self._lock:
            row = self._conn.execute(
                "SELECT github_pat FROM users WHERE id = ?",
                (int(user_id),),
            ).fetchone()
        if row is None:
            return ""
        return str(row["github_pat"] or "").strip()

    def _kb_row_by_name(self, name: str) -> sqlite3.Row | None:
        return self._conn.execute(
            """
            SELECT kb.*, u.username AS owner_username
            FROM knowledge_bases kb
            JOIN users u ON u.id = kb.owner_id
            WHERE kb.name = ? COLLATE NOCASE
            """,
            (name,),
        ).fetchone()

    def _pick_least_used_list_color_unlocked(self) -> int:
        rows = self._conn.execute(
            """
            SELECT COALESCE(list_color_idx, 0) AS cidx, COUNT(*) AS c
            FROM knowledge_bases
            GROUP BY COALESCE(list_color_idx, 0)
            """
        ).fetchall()
        counts = {i: 0 for i in range(5)}
        for r in rows:
            idx = int(r["cidx"])
            if idx < 0 or idx > 4:
                idx = 0
            counts[idx] = int(r["c"])
        return min(range(5), key=lambda i: counts[i])

    def _kb_is_public_unlocked(self, kb: sqlite3.Row) -> bool:
        try:
            return bool(int(kb["is_public"] or 0))
        except (KeyError, TypeError, ValueError):
            return False

    def _permission_unlocked(self, user_id: int, kb: sqlite3.Row) -> KBPermission | None:
        oid = int(kb["owner_id"])
        if user_id == oid:
            return KBPermission(can_read=True, can_write=True, can_delete=True, is_owner=True)
        m = self._conn.execute(
            """
            SELECT can_read, can_write, can_delete FROM kb_members
            WHERE kb_id = ? AND user_id = ?
            """,
            (int(kb["id"]), int(user_id)),
        ).fetchone()
        if m is not None:
            w = bool(m["can_write"])
            return KBPermission(
                can_read=True,
                can_write=w,
                can_delete=False,
                is_owner=False,
            )
        if self._kb_is_public_unlocked(kb):
            return KBPermission(can_read=True, can_write=False, can_delete=False, is_owner=False)
        return None

    def _row_to_record(self, kb: sqlite3.Row, perm: KBPermission) -> KBRecord:
        oid = int(kb["owner_id"])
        try:
            lc = int(kb["list_color_idx"])
        except (KeyError, TypeError, ValueError):
            lc = 0
        lc = max(0, min(4, lc))
        ou = ""
        try:
            if kb["owner_username"] is not None:
                ou = str(kb["owner_username"])
        except (KeyError, TypeError):
            ou = ""
        o_has = self._user_has_avatar_unlocked(oid)
        pub = self._kb_is_public_unlocked(kb)
        try:
            wru = str(kb["webhook_repo_url"] or "")
        except (KeyError, TypeError, ValueError):
            wru = ""
        try:
            wrf = str(kb["webhook_ref"] or "")
        except (KeyError, TypeError, ValueError):
            wrf = ""
        return KBRecord(
            id=int(kb["id"]),
            name=str(kb["name"]),
            description=str(kb["description"] or ""),
            readme_md=str(kb["readme_md"] or ""),
            db_path=str(kb["db_path"]),
            owner_id=oid,
            is_public=pub,
            list_color_idx=lc,
            icon=str(kb["icon"] or "book"),
            source_type=str(kb["source_type"] or "tar"),
            webhook_provider=str(kb["webhook_provider"] or ""),
            webhook_secret=str(kb["webhook_secret"] or ""),
            webhook_repo_url=wru,
            webhook_ref=wrf,
            owner_username=ou,
            owner_has_avatar=o_has,
            permission=perm,
        )

    def create_knowledge_base(
        self,
        *,
        name: str,
        description: str,
        readme_md: str,
        db_path: str,
        owner_id: int,
        is_public: bool = False,
        icon: str = "book",
        source_type: str = "tar",
        webhook_provider: str = "",
        webhook_secret: str = "",
        webhook_repo_url: str = "",
        webhook_ref: str = "",
    ) -> KBRecord:
        t = self._now()
        desc = str(description).strip()
        readme = str(readme_md or "").strip()
        pub_i = 1 if is_public else 0
        icon_key = str(icon or "book").strip() or "book"
        src = str(source_type or "tar").strip().lower() or "tar"
        provider = str(webhook_provider or "").strip().lower()
        secret = str(webhook_secret or "").strip()
        wru = str(webhook_repo_url or "").strip()
        wrf = str(webhook_ref or "").strip()
        with self._lock:
            try:
                color = self._pick_least_used_list_color_unlocked()
                cur = self._conn.execute(
                    """
                    INSERT INTO knowledge_bases(
                        name, description, readme_md, db_path, owner_id, created_at, list_color_idx, is_public, icon,
                        source_type, webhook_provider, webhook_secret, webhook_repo_url, webhook_ref
                    )
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        name,
                        desc,
                        readme,
                        str(db_path),
                        int(owner_id),
                        t,
                        color,
                        pub_i,
                        icon_key,
                        src,
                        provider,
                        secret,
                        wru,
                        wrf,
                    ),
                )
                self._conn.commit()
                kb_id = int(cur.lastrowid)
            except sqlite3.IntegrityError as e:
                self._conn.rollback()
                raise ValueError("Knowledge base name already exists") from e
            kb = self._conn.execute(
                """
                SELECT kb.*, u.username AS owner_username
                FROM knowledge_bases kb
                JOIN users u ON u.id = kb.owner_id
                WHERE kb.id = ?
                """,
                (kb_id,),
            ).fetchone()
        assert kb is not None
        perm = KBPermission(can_read=True, can_write=True, can_delete=True, is_owner=True)
        return self._row_to_record(kb, perm)

    def get_knowledge_base(self, name: str) -> KBRecord | None:
        with self._lock:
            kb = self._kb_row_by_name(name)
            if kb is None:
                return None
            if not self._kb_ready_from_row(kb):
                return None
            perm = KBPermission(can_read=True, can_write=True, can_delete=True, is_owner=True)
            return self._row_to_record(kb, perm)

    def resolve_kb_db_path(self, name: str) -> str | None:
        with self._lock:
            kb = self._kb_row_by_name(name)
            if kb is None:
                return None
            if not self._kb_ready_from_row(kb):
                return None
            return str(kb["db_path"])

    def all_kb_db_paths(self) -> list[Path]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT db_path FROM knowledge_bases WHERE TRIM(COALESCE(db_path, '')) != ''",
            ).fetchall()
        out: list[Path] = []
        for (dp,) in rows:
            try:
                out.append(Path(str(dp)).expanduser().resolve())
            except OSError:
                continue
        return out

    def delete_knowledge_base(self, name: str) -> bool:
        with self._lock:
            kb = self._kb_row_by_name(name)
            if kb is None:
                return False
            self._conn.execute("DELETE FROM knowledge_bases WHERE id = ?", (int(kb["id"]),))
            self._conn.commit()
        return True

    def update_knowledge_base_description(self, name: str, description: str) -> bool:
        desc = str(description).strip()
        with self._lock:
            kb = self._kb_row_by_name(name)
            if kb is None:
                return False
            if not self._kb_ready_from_row(kb):
                return False
            self._conn.execute(
                "UPDATE knowledge_bases SET description = ? WHERE id = ?",
                (desc, int(kb["id"])),
            )
            self._conn.commit()
        return True

    def update_knowledge_base_readme(self, name: str, readme_md: str) -> bool:
        text = str(readme_md or "").strip()
        with self._lock:
            kb = self._kb_row_by_name(name)
            if kb is None:
                return False
            if not self._kb_ready_from_row(kb):
                return False
            self._conn.execute(
                "UPDATE knowledge_bases SET readme_md = ? WHERE id = ?",
                (text, int(kb["id"])),
            )
            self._conn.commit()
        return True

    def update_knowledge_base_public(self, name: str, is_public: bool) -> bool:
        pub_i = 1 if is_public else 0
        with self._lock:
            kb = self._kb_row_by_name(name)
            if kb is None:
                return False
            if not self._kb_ready_from_row(kb):
                return False
            self._conn.execute(
                "UPDATE knowledge_bases SET is_public = ? WHERE id = ?",
                (pub_i, int(kb["id"])),
            )
            self._conn.commit()
        return True

    def update_knowledge_base_icon(self, name: str, icon: str) -> bool:
        icon_key = str(icon or "book").strip() or "book"
        with self._lock:
            kb = self._kb_row_by_name(name)
            if kb is None:
                return False
            if not self._kb_ready_from_row(kb):
                return False
            self._conn.execute(
                "UPDATE knowledge_bases SET icon = ? WHERE id = ?",
                (icon_key, int(kb["id"])),
            )
            self._conn.commit()
        return True

    def update_knowledge_base_webhook_secret(self, name: str, secret: str) -> bool:
        tok = str(secret or "").strip()
        with self._lock:
            kb = self._kb_row_by_name(name)
            if kb is None:
                return False
            if not self._kb_ready_from_row(kb):
                return False
            self._conn.execute(
                "UPDATE knowledge_bases SET webhook_secret = ? WHERE id = ?",
                (tok, int(kb["id"])),
            )
            self._conn.commit()
        return True

    def update_knowledge_base_webhook_source(
        self, name: str, *, repo_url: str | None = None, ref: str | None = None
    ) -> bool:
        with self._lock:
            kb = self._kb_row_by_name(name)
            if kb is None:
                return False
            try:
                cur_ru = str(kb["webhook_repo_url"] or "")
            except (KeyError, TypeError, ValueError):
                cur_ru = ""
            try:
                cur_rf = str(kb["webhook_ref"] or "")
            except (KeyError, TypeError, ValueError):
                cur_rf = ""
            new_ru = str(repo_url).strip() if repo_url is not None else cur_ru
            new_rf = str(ref).strip() if ref is not None else cur_rf
            self._conn.execute(
                "UPDATE knowledge_bases SET webhook_repo_url = ?, webhook_ref = ? WHERE id = ?",
                (new_ru, new_rf, int(kb["id"])),
            )
            self._conn.commit()
        return True

    def save_kb_icon(self, kb_name: str, mime: str, body: bytes) -> bool:
        with self._lock:
            kb = self._kb_row_by_name(kb_name)
            if kb is None:
                return False
            if not self._kb_ready_from_row(kb):
                return False
            kid = int(kb["id"])
            path = self._kb_icon_file(kid)
            path.write_bytes(body)
            self._conn.execute(
                "UPDATE knowledge_bases SET icon = ? WHERE id = ?",
                (str(mime or "").strip(), kid),
            )
            self._conn.commit()
        return True

    def load_kb_icon(self, kb_name: str) -> tuple[str, bytes] | None:
        with self._lock:
            kb = self._kb_row_by_name(kb_name)
            if kb is None:
                return None
            if not self._kb_ready_from_row(kb):
                return None
            kid = int(kb["id"])
            mime = str(kb["icon"] or "").strip()
            if not mime or "/" not in mime:
                return None
            path = self._kb_icon_file(kid)
            if not path.is_file():
                return None
            data = path.read_bytes()
        return mime, data

    def clear_kb_icon(self, kb_name: str) -> bool:
        with self._lock:
            kb = self._kb_row_by_name(kb_name)
            if kb is None:
                return False
            if not self._kb_ready_from_row(kb):
                return False
            kid = int(kb["id"])
            path = self._kb_icon_file(kid)
            try:
                if path.exists():
                    path.unlink()
            except OSError:
                pass
            self._conn.execute(
                "UPDATE knowledge_bases SET icon = ? WHERE id = ?",
                ("book", kid),
            )
            self._conn.commit()
        return True

    def rename_knowledge_base(self, old_name: str, new_name: str) -> bool:
        old_key = str(old_name).strip()
        new_key = str(new_name).strip()
        if not old_key or not new_key:
            return False
        with self._lock:
            kb = self._kb_row_by_name(old_key)
            if kb is None:
                return False
            if not self._kb_ready_from_row(kb):
                return False
            try:
                self._conn.execute(
                    "UPDATE knowledge_bases SET name = ? WHERE id = ?",
                    (new_key, int(kb["id"])),
                )
                self._conn.commit()
            except sqlite3.IntegrityError as e:
                self._conn.rollback()
                raise ValueError("Knowledge base name already exists") from e
        return True

    def list_knowledge_bases_for_user(self, user_id: int) -> list[KBRecord]:
        out: list[KBRecord] = []
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT kb.*, u.username AS owner_username
                FROM knowledge_bases kb
                JOIN users u ON u.id = kb.owner_id
                WHERE COALESCE(kb.kb_ready, 1) = 1
                ORDER BY kb.name COLLATE NOCASE
                """
            ).fetchall()
            for kb in rows:
                perm = self._permission_unlocked(int(user_id), kb)
                if perm is None or not perm.can_read:
                    continue
                out.append(self._row_to_record(kb, perm))
        return out

    def list_all_knowledge_bases(self) -> list[KBRecord]:
        out: list[KBRecord] = []
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT kb.*, u.username AS owner_username
                FROM knowledge_bases kb
                JOIN users u ON u.id = kb.owner_id
                WHERE COALESCE(kb.kb_ready, 1) = 1
                ORDER BY kb.name COLLATE NOCASE
                """
            ).fetchall()
            for kb in rows:
                perm = KBPermission(can_read=True, can_write=True, can_delete=True, is_owner=True)
                out.append(self._row_to_record(kb, perm))
        return out

    def permission_for(self, user_id: int, kb_name: str) -> KBPermission | None:
        with self._lock:
            kb = self._kb_row_by_name(kb_name)
            if kb is None:
                return None
            if not self._kb_ready_from_row(kb):
                return None
            return self._permission_unlocked(int(user_id), kb)

    def list_members_roster(self, kb_name: str) -> list[dict] | None:
        with self._lock:
            kb = self._kb_row_by_name(kb_name)
            if kb is None:
                return None
            if not self._kb_ready_from_row(kb):
                return None
            owner = self._conn.execute(
                "SELECT username FROM users WHERE id = ?",
                (int(kb["owner_id"]),),
            ).fetchone()
            members = self._conn.execute(
                """
                SELECT u.username, m.can_read, m.can_write, m.can_delete
                FROM kb_members m
                JOIN users u ON u.id = m.user_id
                WHERE m.kb_id = ?
                ORDER BY u.username COLLATE NOCASE
                """,
                (int(kb["id"]),),
            ).fetchall()
        o_name = str(owner["username"]) if owner else ""
        result: list[dict] = [
            {
                "username": o_name,
                "role": "owner",
                "can_read": True,
                "can_write": True,
                "can_delete": True,
            }
        ]
        for r in members:
            result.append(
                {
                    "username": str(r["username"]),
                    "role": "member",
                    "can_read": True,
                    "can_write": bool(r["can_write"]),
                    "can_delete": False,
                }
            )
        return result

    def kb_subscription_get(self, user_id: int, kb_name: str) -> bool:
        with self._lock:
            kb = self._kb_row_by_name(kb_name)
            if kb is None:
                return False
            if not self._kb_ready_from_row(kb):
                return False
            row = self._conn.execute(
                "SELECT 1 FROM kb_subscriptions WHERE user_id = ? AND kb_id = ? LIMIT 1",
                (int(user_id), int(kb["id"])),
            ).fetchone()
            return row is not None

    def kb_subscription_set(self, user_id: int, kb_name: str, subscribed: bool) -> bool:
        with self._lock:
            kb = self._kb_row_by_name(kb_name)
            if kb is None:
                return False
            if not self._kb_ready_from_row(kb):
                return False
            uid = int(user_id)
            kid = int(kb["id"])
            if subscribed:
                self._conn.execute(
                    "INSERT OR REPLACE INTO kb_subscriptions(user_id, kb_id, created_at) VALUES (?,?,?)",
                    (uid, kid, self._now()),
                )
            else:
                self._conn.execute(
                    "DELETE FROM kb_subscriptions WHERE user_id = ? AND kb_id = ?",
                    (uid, kid),
                )
            self._conn.commit()
        return True

    def list_subscribed_knowledge_bases_for_user(self, user_id: int) -> list[KBRecord]:
        out: list[KBRecord] = []
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT kb.*, u.username AS owner_username
                FROM kb_subscriptions s
                JOIN knowledge_bases kb ON kb.id = s.kb_id
                JOIN users u ON u.id = kb.owner_id
                WHERE s.user_id = ? AND COALESCE(kb.kb_ready, 1) = 1
                ORDER BY kb.name COLLATE NOCASE
                """,
                (int(user_id),),
            ).fetchall()
            for kb in rows:
                perm = self._permission_unlocked(int(user_id), kb)
                if perm is None or not perm.can_read:
                    continue
                out.append(self._row_to_record(kb, perm))
        return out

    def list_owned_and_subscribed_knowledge_bases_for_user(self, user_id: int) -> list[KBRecord]:
        out: list[KBRecord] = []
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT DISTINCT kb.*, u.username AS owner_username
                FROM knowledge_bases kb
                JOIN users u ON u.id = kb.owner_id
                LEFT JOIN kb_subscriptions s ON s.kb_id = kb.id AND s.user_id = ?
                WHERE COALESCE(kb.kb_ready, 1) = 1
                  AND (kb.owner_id = ? OR s.user_id IS NOT NULL)
                ORDER BY kb.name COLLATE NOCASE
                """,
                (int(user_id), int(user_id)),
            ).fetchall()
            for kb in rows:
                perm = self._permission_unlocked(int(user_id), kb)
                if perm is None or not perm.can_read:
                    continue
                out.append(self._row_to_record(kb, perm))
        return out

    def get_api_key_owner_user_id(self, key_value: str) -> int | None:
        kv = str(key_value or "").strip()
        if not kv:
            return None
        with self._lock:
            row = self._conn.execute(
                "SELECT user_id FROM api_keys WHERE key_value = ? LIMIT 1",
                (kv,),
            ).fetchone()
        if row is None:
            return None
        return int(row["user_id"])

    def list_api_keys_for_user(self, user_id: int) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT id, name, key_value, created_at
                FROM api_keys
                WHERE user_id = ?
                ORDER BY id DESC
                """,
                (int(user_id),),
            ).fetchall()
        out: list[dict] = []
        for r in rows:
            out.append(
                {
                    "id": int(r["id"]),
                    "name": str(r["name"] or ""),
                    "key": str(r["key_value"] or ""),
                    "created_at": float(r["created_at"] or 0.0),
                }
            )
        return out

    def create_api_key_for_user(self, user_id: int, *, name: str, key_value: str) -> dict | None:
        nm = str(name or "").strip()
        kv = str(key_value or "").strip()
        with self._lock:
            cnt = self._conn.execute(
                "SELECT COUNT(*) AS c FROM api_keys WHERE user_id = ?",
                (int(user_id),),
            ).fetchone()
            c = int(cnt["c"] or 0)
            if c >= 3:
                return None
            if not nm:
                rows = self._conn.execute(
                    "SELECT name FROM api_keys WHERE user_id = ?",
                    (int(user_id),),
                ).fetchall()
                mx = 0
                for r in rows:
                    s = str(r["name"] or "").strip().lower()
                    if not s.startswith("apikey-"):
                        continue
                    try:
                        n = int(s.split("-", 1)[1])
                    except (ValueError, IndexError):
                        continue
                    mx = max(mx, n)
                nm = f"apikey-{mx + 1}"
            cur = self._conn.execute(
                "INSERT INTO api_keys(user_id, name, key_value, created_at) VALUES(?,?,?,?)",
                (int(user_id), nm, kv, self._now()),
            )
            kid = int(cur.lastrowid)
            self._conn.commit()
            row = self._conn.execute(
                "SELECT id, name, key_value, created_at FROM api_keys WHERE id = ?",
                (kid,),
            ).fetchone()
        if row is None:
            return None
        return {
            "id": int(row["id"]),
            "name": str(row["name"] or ""),
            "key": str(row["key_value"] or ""),
            "created_at": float(row["created_at"] or 0.0),
        }

    def delete_api_key_for_user(self, user_id: int, key_id: int) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM api_keys WHERE user_id = ? AND id = ?",
                (int(user_id), int(key_id)),
            )
            n = int(cur.rowcount or 0)
            self._conn.commit()
        return n > 0

    def upsert_member(
        self,
        kb_name: str,
        *,
        actor_user_id: int,
        member_username: str,
        can_read: bool,
        can_write: bool,
        can_delete: bool,
    ) -> bool:
        uname = member_username.strip()
        with self._lock:
            kb = self._kb_row_by_name(kb_name)
            if kb is None:
                return False
            if not self._kb_ready_from_row(kb):
                return False
            if int(kb["owner_id"]) != int(actor_user_id):
                return False
            target = self._conn.execute(
                "SELECT id FROM users WHERE username = ? COLLATE NOCASE",
                (uname,),
            ).fetchone()
            if target is None:
                return False
            tid = int(target["id"])
            if tid == int(kb["owner_id"]):
                return False
            can_read = True
            can_delete = False
            self._conn.execute(
                """
                INSERT INTO kb_members(kb_id, user_id, can_read, can_write, can_delete)
                VALUES(?,?,?,?,?)
                ON CONFLICT(kb_id, user_id) DO UPDATE SET
                    can_read=excluded.can_read,
                    can_write=excluded.can_write,
                    can_delete=excluded.can_delete
                """,
                (
                    int(kb["id"]),
                    tid,
                    1 if can_read else 0,
                    1 if can_write else 0,
                    1 if can_delete else 0,
                ),
            )
            self._conn.commit()
        return True

    def remove_member(self, kb_name: str, *, actor_user_id: int, member_username: str) -> bool:
        uname = member_username.strip()
        with self._lock:
            kb = self._kb_row_by_name(kb_name)
            if kb is None:
                return False
            if not self._kb_ready_from_row(kb):
                return False
            if int(kb["owner_id"]) != int(actor_user_id):
                return False
            target = self._conn.execute(
                "SELECT id FROM users WHERE username = ? COLLATE NOCASE",
                (uname,),
            ).fetchone()
            if target is None:
                return False
            tid = int(target["id"])
            if tid == int(kb["owner_id"]):
                return False
            cur = self._conn.execute(
                "DELETE FROM kb_members WHERE kb_id = ? AND user_id = ?",
                (int(kb["id"]), tid),
            )
            n = cur.rowcount
            self._conn.commit()
        return n > 0

    def knowledge_base_name_taken(self, name: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM knowledge_bases WHERE name = ? COLLATE NOCASE LIMIT 1",
                (str(name).strip(),),
            ).fetchone()
        return row is not None

    def create_pending_knowledge_base(
        self,
        *,
        name: str,
        description: str,
        readme_md: str,
        db_path: str,
        owner_id: int,
        is_public: bool = False,
        icon: str = "book",
        source_type: str = "tar",
        webhook_provider: str = "",
        webhook_secret: str = "",
        webhook_repo_url: str = "",
        webhook_ref: str = "",
    ) -> None:
        t = self._now()
        desc = str(description).strip()
        readme = str(readme_md or "").strip()
        pub_i = 1 if is_public else 0
        icon_key = str(icon or "book").strip() or "book"
        src = str(source_type or "tar").strip().lower() or "tar"
        provider = str(webhook_provider or "").strip().lower()
        secret = str(webhook_secret or "").strip()
        wru = str(webhook_repo_url or "").strip()
        wrf = str(webhook_ref or "").strip()
        with self._lock:
            color = self._pick_least_used_list_color_unlocked()
            self._conn.execute(
                """
                INSERT INTO knowledge_bases(
                    name, description, readme_md, db_path, owner_id, created_at,
                    list_color_idx, is_public, icon, kb_ready, source_type, webhook_provider, webhook_secret,
                    webhook_repo_url, webhook_ref
                )
                VALUES(?,?,?,?,?,?,?,?,?,0,?,?,?,?,?)
                """,
                (
                    name,
                    desc,
                    readme,
                    str(db_path),
                    int(owner_id),
                    t,
                    color,
                    pub_i,
                    icon_key,
                    src,
                    provider,
                    secret,
                    wru,
                    wrf,
                ),
            )
            self._conn.commit()

    def get_kb_record_any_state(self, name: str) -> KBRecord | None:
        with self._lock:
            kb = self._kb_row_by_name(name)
            if kb is None:
                return None
            perm = KBPermission(can_read=True, can_write=True, can_delete=True, is_owner=True)
            return self._row_to_record(kb, perm)

    def finalize_knowledge_base_ready(self, name: str) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "UPDATE knowledge_bases SET kb_ready = 1 WHERE name = ? COLLATE NOCASE",
                (str(name).strip(),),
            )
            self._conn.commit()
        return int(cur.rowcount or 0) > 0

    def count_user_upload_tasks_active(self, user_id: int) -> int:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT COUNT(*) AS c FROM build_jobs
                WHERE user_id = ? AND task_kind = 'upload'
                  AND status IN ('queued', 'running')
                """,
                (int(user_id),),
            ).fetchone()
        return int(row["c"] or 0) if row else 0

    def enqueue_build_job(
        self,
        *,
        job_id: str,
        user_id: int,
        task_kind: str,
        op: str,
        kb_name: str,
        upload_id: str,
        payload: dict[str, Any],
    ) -> None:
        t = self._now()
        payload_json = json.dumps(payload, ensure_ascii=False)
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO build_jobs(
                    job_id, user_id, task_kind, op, kb_name, status,
                    cancel_requested, created_at, percent, phase, detail,
                    upload_id, payload_json
                )
                VALUES(?,?,?,?,?,'queued',0,?,0,'queued','',?,?)
                """,
                (
                    job_id,
                    int(user_id),
                    str(task_kind),
                    str(op),
                    str(kb_name),
                    t,
                    str(upload_id),
                    payload_json,
                ),
            )
            self._conn.commit()

    def _build_job_row_to_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        result = None
        if row["result_json"]:
            try:
                result = json.loads(str(row["result_json"]))
            except json.JSONDecodeError:
                result = None
        return {
            "job_id": str(row["job_id"]),
            "user_id": int(row["user_id"]),
            "task_kind": str(row["task_kind"]),
            "op": str(row["op"]),
            "kb_name": str(row["kb_name"]),
            "status": str(row["status"]),
            "cancel_requested": bool(int(row["cancel_requested"] or 0)),
            "created_at": float(row["created_at"] or 0.0),
            "started_at": float(row["started_at"]) if row["started_at"] is not None else None,
            "finished_at": float(row["finished_at"]) if row["finished_at"] is not None else None,
            "percent": int(row["percent"] or 0),
            "phase": str(row["phase"] or ""),
            "detail": str(row["detail"] or ""),
            "error": str(row["error"]) if row["error"] else None,
            "result": result,
            "upload_id": str(row["upload_id"] or ""),
            "payload": json.loads(str(row["payload_json"] or "{}")),
        }

    def get_build_job(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM build_jobs WHERE job_id = ?",
                (str(job_id).strip(),),
            ).fetchone()
        if row is None:
            return None
        return self._build_job_row_to_dict(row)

    def build_job_cancel_requested(self, job_id: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT cancel_requested FROM build_jobs WHERE job_id = ?",
                (str(job_id).strip(),),
            ).fetchone()
        return bool(row and int(row["cancel_requested"] or 0))

    def list_build_jobs_for_user(self, user_id: int, *, limit: int = 200) -> list[dict[str, Any]]:
        lim = max(1, min(500, int(limit)))
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT * FROM build_jobs
                WHERE user_id = ? AND status IN ('queued', 'running')
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (int(user_id), lim),
            ).fetchall()
        return [self._build_job_row_to_dict(r) for r in rows]

    def delete_build_job(self, job_id: str) -> bool:
        jid = str(job_id).strip()
        with self._lock:
            cur = self._conn.execute("DELETE FROM build_jobs WHERE job_id = ?", (jid,))
            self._conn.commit()
        return int(cur.rowcount or 0) > 0

    def update_build_job_fields(
        self,
        job_id: str,
        *,
        status: str | None = None,
        phase: str | None = None,
        percent: int | None = None,
        detail: str | None = None,
        error: str | None = None,
        result: dict[str, Any] | None = None,
        cancel_requested: bool | None = None,
        started_at: float | None = None,
        finished_at: float | None = None,
    ) -> bool:
        fields: list[str] = []
        vals: list[Any] = []
        if status is not None:
            fields.append("status = ?")
            vals.append(str(status))
        if phase is not None:
            fields.append("phase = ?")
            vals.append(str(phase))
        if percent is not None:
            fields.append("percent = ?")
            vals.append(int(percent))
        if detail is not None:
            fields.append("detail = ?")
            vals.append(str(detail))
        if error is not None:
            fields.append("error = ?")
            vals.append(str(error))
        if result is not None:
            fields.append("result_json = ?")
            vals.append(json.dumps(result, ensure_ascii=False))
        if cancel_requested is not None:
            fields.append("cancel_requested = ?")
            vals.append(1 if cancel_requested else 0)
        if started_at is not None:
            fields.append("started_at = ?")
            vals.append(float(started_at))
        if finished_at is not None:
            fields.append("finished_at = ?")
            vals.append(float(finished_at))
        if not fields:
            return False
        vals.append(str(job_id).strip())
        with self._lock:
            cur = self._conn.execute(
                f"UPDATE build_jobs SET {', '.join(fields)} WHERE job_id = ?",
                vals,
            )
            self._conn.commit()
        return int(cur.rowcount or 0) > 0

    def claim_next_queued_build_job(self) -> dict[str, Any] | None:
        """Atomically claim the oldest queued job under ``_lock`` (single shared connection).

        Avoid ``BEGIN IMMEDIATE`` here: sqlite3 may already have an implicit/open transaction,
        which raises ``OperationalError: cannot start a transaction within a transaction``.
        """
        t = self._now()
        row2 = None
        with self._lock:
            try:
                row = self._conn.execute(
                    """
                    SELECT job_id FROM build_jobs
                    WHERE status = 'queued'
                    ORDER BY created_at ASC
                    LIMIT 1
                    """
                ).fetchone()
                if row is None:
                    self._conn.commit()
                    return None
                jid = str(row["job_id"])
                cur = self._conn.execute(
                    """
                    UPDATE build_jobs
                    SET status = 'running', started_at = ?, phase = 'extract', percent = 1
                    WHERE job_id = ? AND status = 'queued'
                    """,
                    (t, jid),
                )
                if int(cur.rowcount or 0) == 0:
                    self._conn.rollback()
                    return None
                row2 = self._conn.execute(
                    "SELECT * FROM build_jobs WHERE job_id = ?",
                    (jid,),
                ).fetchone()
                self._conn.commit()
            except Exception:
                try:
                    self._conn.rollback()
                except sqlite3.Error:
                    pass
                raise
        if row2 is None:
            return None
        return self._build_job_row_to_dict(row2)

    def request_cancel_build_job(
        self, job_id: str, user_id: int
    ) -> tuple[str | None, dict[str, Any] | None]:
        """On success: (None, None) for running job (cancel flag set), or (None, meta) if queued job removed.

        ``meta`` may include ``dropped_queued``, ``op``, ``kb_name``, ``upload_id`` for HTTP cleanup.
        On failure: (error_message, None).
        """
        jid = str(job_id).strip()
        with self._lock:
            row = self._conn.execute(
                "SELECT user_id, status, op, kb_name, upload_id FROM build_jobs WHERE job_id = ?",
                (jid,),
            ).fetchone()
            if row is None:
                return ("Unknown job", None)
            if int(row["user_id"]) != int(user_id):
                return ("Forbidden", None)
            st = str(row["status"])
            if st in ("done", "error", "cancelled"):
                return ("Job already finished", None)
            if st == "queued":
                op = str(row["op"] or "")
                kb_name = str(row["kb_name"] or "")
                upload_id = str(row["upload_id"] or "")
                self._conn.execute("DELETE FROM build_jobs WHERE job_id = ?", (jid,))
                self._conn.commit()
                return (
                    None,
                    {
                        "dropped_queued": True,
                        "op": op,
                        "kb_name": kb_name,
                        "upload_id": upload_id,
                    },
                )
            self._conn.execute(
                "UPDATE build_jobs SET cancel_requested = 1 WHERE job_id = ?",
                (jid,),
            )
            self._conn.commit()
        return (None, None)
