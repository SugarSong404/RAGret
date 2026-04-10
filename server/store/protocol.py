"""Abstract application store for future MySQL/Redis backends."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class UserRecord:
    id: int
    username: str


@dataclass(frozen=True)
class KBPermission:
    can_read: bool
    can_write: bool
    can_delete: bool
    is_owner: bool


@dataclass(frozen=True)
class KBRecord:
    id: int
    name: str
    description: str
    readme_md: str
    db_path: str
    owner_id: int
    is_public: bool
    list_color_idx: int
    icon: str
    source_type: str
    webhook_provider: str
    webhook_secret: str
    webhook_repo_url: str
    webhook_ref: str
    owner_username: str
    owner_has_avatar: bool
    permission: KBPermission


class AppStore(Protocol):
    def close(self) -> None: ...

    def create_user(self, username: str, password_hash: str) -> UserRecord: ...

    def get_user_by_username(self, username: str) -> UserRecord | None: ...

    def get_user_by_id(self, user_id: int) -> UserRecord | None: ...

    def verify_user_password(self, username: str, password: str) -> UserRecord | None: ...

    def create_session(self, user_id: int, *, ttl_seconds: int) -> str: ...

    def get_session_user_id(self, token: str) -> int | None: ...

    def delete_session(self, token: str) -> None: ...

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
    ) -> KBRecord: ...

    def get_knowledge_base(self, name: str) -> KBRecord | None: ...

    def resolve_kb_db_path(self, name: str) -> str | None: ...

    def delete_knowledge_base(self, name: str) -> bool: ...

    def update_knowledge_base_description(self, name: str, description: str) -> bool: ...

    def update_knowledge_base_readme(self, name: str, readme_md: str) -> bool: ...

    def update_knowledge_base_public(self, name: str, is_public: bool) -> bool: ...

    def update_knowledge_base_icon(self, name: str, icon: str) -> bool: ...

    def update_knowledge_base_webhook_secret(self, name: str, secret: str) -> bool: ...

    def update_knowledge_base_webhook_source(
        self, name: str, *, repo_url: str | None = None, ref: str | None = None
    ) -> bool: ...

    def save_kb_icon(self, kb_name: str, mime: str, body: bytes) -> bool: ...

    def load_kb_icon(self, kb_name: str) -> tuple[str, bytes] | None: ...

    def clear_kb_icon(self, kb_name: str) -> bool: ...

    def rename_knowledge_base(self, old_name: str, new_name: str) -> bool: ...

    def list_knowledge_bases_for_user(self, user_id: int) -> list[KBRecord]: ...

    def list_all_knowledge_bases(self) -> list[KBRecord]: ...

    def permission_for(self, user_id: int, kb_name: str) -> KBPermission | None: ...

    def list_members_roster(self, kb_name: str) -> list[dict] | None:
        """Return owner + members for display. None if the KB name is unknown. Caller must enforce read permission."""
        ...

    def upsert_member(
        self,
        kb_name: str,
        *,
        actor_user_id: int,
        member_username: str,
        can_read: bool,
        can_write: bool,
        can_delete: bool,
    ) -> bool: ...

    def remove_member(self, kb_name: str, *, actor_user_id: int, member_username: str) -> bool: ...

    def kb_subscription_get(self, user_id: int, kb_name: str) -> bool: ...

    def kb_subscription_set(self, user_id: int, kb_name: str, subscribed: bool) -> bool: ...

    def list_subscribed_knowledge_bases_for_user(self, user_id: int) -> list[KBRecord]: ...

    def list_owned_and_subscribed_knowledge_bases_for_user(self, user_id: int) -> list[KBRecord]: ...

    def get_kb_record_any_state(self, name: str) -> KBRecord | None: ...

    def get_api_key_owner_user_id(self, key_value: str) -> int | None: ...

    def list_api_keys_for_user(self, user_id: int) -> list[dict]: ...

    def create_api_key_for_user(self, user_id: int, *, name: str, key_value: str) -> dict | None: ...

    def delete_api_key_for_user(self, user_id: int, key_id: int) -> bool: ...

    def get_api_key_owner(self, key_value: str) -> int | None: ...

    def list_api_key_scoped_knowledge_bases_for_user(self, user_id: int) -> list[KBRecord]: ...

    def rename_knowledge_base(self, old_name: str, new_name: str) -> bool: ...

    def change_password(self, user_id: int, current_password: str, new_password_hash: str) -> bool: ...

    def user_has_avatar(self, user_id: int) -> bool: ...

    def save_avatar(self, user_id: int, mime: str, body: bytes) -> None: ...

    def load_avatar(self, user_id: int) -> tuple[str, bytes] | None: ...

    def clear_avatar(self, user_id: int) -> None: ...

    def set_user_gitlab_pat(self, user_id: int, pat: str) -> None: ...

    def get_user_gitlab_pat(self, user_id: int) -> str: ...
