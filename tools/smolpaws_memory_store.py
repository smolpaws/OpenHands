"""Bounded filesystem store shared by insider SmolPaws conversations."""

from __future__ import annotations

import hashlib
import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from filelock import FileLock


MAX_DURABLE_BYTES = 64 * 1024
MAX_DAILY_ENTRY_BYTES = 16 * 1024
MAX_DAILY_FILE_BYTES = 128 * 1024
MAX_DAILY_LIST_ENTRIES = 366
_DEFAULT_DURABLE = "# SmolPaws Memory\n"
_DATE_FORMAT = "%Y-%m-%d"


@dataclass(frozen=True)
class MemoryDocument:
    content: str
    revision: str


@dataclass(frozen=True)
class DailyDocument:
    date: str
    content: str
    revision: str


@dataclass(frozen=True)
class DailySummary:
    date: str
    size_bytes: int
    revision: str


class MemoryConflictError(Exception):
    def __init__(self, current_revision: str):
        super().__init__("durable memory changed since it was read")
        self.current_revision = current_revision


def memory_root() -> Path:
    persistence_dir = os.environ.get("OH_PERSISTENCE_DIR")
    base = (
        Path(persistence_dir).expanduser()
        if persistence_dir
        else Path.home() / ".openhands"
    )
    return base / "smolpaws" / "memory"


class SmolPawsMemoryStore:
    def __init__(self, root: Path | None = None):
        self.root = root if root is not None else memory_root()
        self._lock = FileLock(str(self.root / ".memory.lock"), timeout=10, mode=0o600)

    def read_durable(self) -> MemoryDocument:
        with self._locked():
            self._ensure_store()
            return self._read_memory_document(self.root / "MEMORY.md")

    def replace_durable(
        self, content: str, *, expected_revision: str
    ) -> MemoryDocument:
        encoded = _bounded_utf8(content, MAX_DURABLE_BYTES, "durable memory")
        if not expected_revision:
            raise ValueError("expected_revision is required")

        with self._locked():
            self._ensure_store()
            path = self.root / "MEMORY.md"
            current = self._read_memory_document(path)
            if current.revision != expected_revision:
                raise MemoryConflictError(current.revision)
            self._atomic_write(path, encoded)
            return MemoryDocument(content=content, revision=_revision(encoded))

    def append_today(self, content: str) -> DailyDocument:
        entry = content.strip()
        if not entry:
            raise ValueError("daily entry must not be empty")
        entry_bytes = _bounded_utf8(entry, MAX_DAILY_ENTRY_BYTES, "daily entry")
        today = date.today().isoformat()

        with self._locked():
            self._ensure_store()
            path = self._daily_path(today)
            if path.exists():
                self._reject_symlink(path)
                if os.name != "nt":
                    path.chmod(0o600)
                if path.stat().st_size > MAX_DAILY_FILE_BYTES:
                    raise ValueError("daily file exceeds maximum size")
                existing = path.read_bytes()
                if len(existing) > MAX_DAILY_FILE_BYTES:
                    raise ValueError("daily file exceeds maximum size")
                try:
                    existing.decode("utf-8")
                except UnicodeDecodeError as error:
                    raise ValueError("memory file must be valid UTF-8") from error
                separator = (
                    b""
                    if existing.endswith(b"\n\n")
                    else (b"\n" if existing.endswith(b"\n") else b"\n\n")
                )
            else:
                existing = f"# {today}\n\n".encode()
                separator = b""
            updated = existing + separator + entry_bytes + b"\n"
            if len(updated) > MAX_DAILY_FILE_BYTES:
                raise ValueError("daily file exceeds maximum size")
            self._atomic_write(path, updated)
            return DailyDocument(
                date=today,
                content=updated.decode("utf-8"),
                revision=_revision(updated),
            )

    def read_daily(self, value: str) -> DailyDocument | None:
        normalized = _validate_date(value)
        with self._locked():
            self._ensure_store()
            path = self._daily_path(normalized)
            if not path.exists():
                return None
            document = self._read_memory_document(path)
            return DailyDocument(
                date=normalized,
                content=document.content,
                revision=document.revision,
            )

    def list_dailies(self) -> list[DailySummary]:
        with self._locked():
            self._ensure_store()
            dated_paths: list[tuple[str, Path]] = []
            for path in self.root.iterdir():
                if not path.is_file() or path.is_symlink() or path.suffix != ".md":
                    continue
                try:
                    normalized = _validate_date(path.stem)
                except ValueError:
                    continue
                dated_paths.append((normalized, path))

            summaries: list[DailySummary] = []
            for normalized, path in sorted(dated_paths, reverse=True)[
                :MAX_DAILY_LIST_ENTRIES
            ]:
                document = self._read_memory_document(path)
                summaries.append(
                    DailySummary(
                        date=normalized,
                        size_bytes=len(document.content.encode("utf-8")),
                        revision=document.revision,
                    )
                )
            return summaries

    @contextmanager
    def _locked(self) -> Iterator[None]:
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if os.name != "nt":
            self.root.chmod(0o700)
        with self._lock:
            yield

    def _ensure_store(self) -> None:
        path = self.root / "MEMORY.md"
        if path.exists():
            self._reject_symlink(path)
            return
        self._atomic_write(path, _DEFAULT_DURABLE.encode())

    def _daily_path(self, value: str) -> Path:
        return self.root / f"{value}.md"

    def _read_memory_document(self, path: Path) -> MemoryDocument:
        self._reject_symlink(path)
        if os.name != "nt":
            path.chmod(0o600)
        limit = MAX_DURABLE_BYTES if path.name == "MEMORY.md" else MAX_DAILY_FILE_BYTES
        if path.stat().st_size > limit:
            raise ValueError("memory file exceeds maximum size")
        data = path.read_bytes()
        if len(data) > limit:
            raise ValueError("memory file exceeds maximum size")
        try:
            content = data.decode("utf-8")
        except UnicodeDecodeError as error:
            raise ValueError("memory file must be valid UTF-8") from error
        return MemoryDocument(content=content, revision=_revision(data))

    @staticmethod
    def _reject_symlink(path: Path) -> None:
        if path.is_symlink():
            raise ValueError("memory files must not be symbolic links")

    @staticmethod
    def _atomic_write(path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as temporary_file:
                temporary_file.write(data)
                temporary_file.flush()
                os.fsync(temporary_file.fileno())
            os.replace(temporary_path, path)
        finally:
            temporary_path.unlink(missing_ok=True)


def _bounded_utf8(content: str, limit: int, label: str) -> bytes:
    try:
        encoded = content.encode("utf-8")
    except UnicodeEncodeError as error:
        raise ValueError(f"{label} must be valid UTF-8") from error
    if len(encoded) > limit:
        raise ValueError(f"{label} exceeds maximum size of {limit} bytes")
    return encoded


def _revision(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _validate_date(value: str) -> str:
    try:
        parsed = datetime.strptime(value, _DATE_FORMAT).date()
    except (TypeError, ValueError) as error:
        raise ValueError("date must be a valid YYYY-MM-DD value") from error
    normalized = parsed.isoformat()
    if normalized != value:
        raise ValueError("date must be a valid YYYY-MM-DD value")
    return normalized
