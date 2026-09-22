"""
Account persistence for StarCourse.

Stores user profiles as a single JSON file on disk.
The storage location is auto-discovered across multiple candidate directories
so the app works in both development and packaged (frozen) modes.
"""
from __future__ import annotations

import os
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime

from .log_manager import log
from .utils.atomic_io import atomic_write_json, load_json_safe


@dataclass
class AccountProfile:
    """A single user account."""
    username: str
    password: str
    nickname: str = ""
    status: str = "idle"
    progress: str = ""
    target_course_name: str = ""
    cookie: str = ""
    cookie_updated_at: str = ""
    created_at: str = ""
    last_login: str = ""
    extra: dict = field(default_factory=dict)

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class AccountStore:
    """CRUD manager for ``AccountProfile`` objects, backed by JSON."""

    _FILENAME = "profiles.json"

    def __init__(self):
        self._profiles: list[AccountProfile] = []
        self._store_path = self._resolve_store_path()
        self.reload()

    # ---- path resolution ----
    def _resolve_store_path(self) -> str:
        candidates: list[str] = []

        if getattr(sys, "frozen", False):
            exe_dir = os.path.dirname(sys.executable)
            candidates.append(os.path.join(exe_dir, self._FILENAME))

        for env_var in ("LOCALAPPDATA", "APPDATA", "USERPROFILE"):
            base = os.environ.get(env_var, "")
            if base:
                candidates.append(os.path.join(base, "StarCourse", self._FILENAME))

        project_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        candidates.append(os.path.join(project_dir, self._FILENAME))

        for path in candidates:
            if os.path.exists(path):
                log.info(f"Profile store located: {path}")
                return path

        for path in candidates:
            try:
                parent = os.path.dirname(path)
                if parent and not os.path.exists(parent):
                    os.makedirs(parent, exist_ok=True)
                log.info(f"Profile store will be created at: {path}")
                return path
            except OSError as exc:
                log.warning(f"Cannot create directory for {path}: {exc}")

        fallback = os.path.join(os.getcwd(), self._FILENAME)
        log.warning(f"Falling back to working directory: {fallback}")
        return fallback

    # ---- CRUD ----
    def reload(self) -> None:
        data = load_json_safe(self._store_path, default=[])
        self._profiles = [AccountProfile(**item) for item in data if isinstance(item, dict)]

    def save(self) -> None:
        atomic_write_json(self._store_path, [asdict(p) for p in self._profiles])

    def get_all(self) -> list[AccountProfile]:
        return self._profiles

    def get(self, username: str) -> AccountProfile | None:
        for p in self._profiles:
            if p.username == username:
                return p
        return None

    def add(self, username: str, password: str, nickname: str = "") -> bool:
        if self.get(username):
            return False
        self._profiles.append(AccountProfile(username=username, password=password, nickname=nickname))
        self.save()
        log.info(f"Profile added: {username}")
        return True

    def remove(self, username: str) -> bool:
        original = len(self._profiles)
        self._profiles = [p for p in self._profiles if p.username != username]
        if len(self._profiles) < original:
            self.save()
            log.info(f"Profile removed: {username}")
            return True
        return False

    def clear_all(self) -> None:
        self._profiles.clear()
        self.save()
        log.info("All profiles cleared")

    def update_status(self, username: str, status: str, progress: str = "") -> None:
        p = self.get(username)
        if p:
            p.status = status
            p.progress = progress
            self.save()

    def update_password(self, username: str, password: str) -> None:
        p = self.get(username)
        if p:
            p.password = password
            self.save()

    def update_nickname(self, username: str, nickname: str) -> None:
        p = self.get(username)
        if p:
            p.nickname = nickname
            self.save()

    def update_cookie(self, username: str, cookie: str) -> None:
        p = self.get(username)
        if p:
            p.cookie = cookie
            p.cookie_updated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self.save()
            log.info(f"Cookie saved for {username} ({len(cookie)} chars)")

    def remove_cookie(self, username: str) -> None:
        p = self.get(username)
        if p:
            p.cookie = ""
            p.cookie_updated_at = ""
            self.save()
