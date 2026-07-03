import json
import shutil
import threading
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Optional

from astrbot.api import logger


def _safe_part(value: str, fallback: str) -> str:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in value)
    return safe or fallback


def _namespace_id(namespace: Optional[str]) -> str:
    if not namespace:
        return "global"
    digest = sha256(namespace.encode("utf-8")).hexdigest()[:16]
    return _safe_part(namespace, "namespace")[:80] + "_" + digest


def _filename_from_key(key: str) -> str:
    normalized = str(key).replace("\\", "/")
    basename = PurePosixPath(normalized).name.replace("\x00", "").strip()
    return _safe_filename(basename, "file")


def _safe_filename(value: str, fallback: str) -> str:
    basename = str(value or "").replace("\x00", "").strip()
    for char in ':*?"<>|':
        basename = basename.replace(char, "_")
    if basename in {"", ".", ".."}:
        basename = fallback
    return basename


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _parse_utc(value: str) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


class FileStorageBackend:
    """File storage backend with per-namespace JSON indexes and blob files."""

    def __init__(self, data_dir: str, store_name: str = "file_store"):
        self.root = Path(data_dir) / store_name
        self.index_dir = self.root / "indexes"
        self.blob_dir = self.root / "blobs"
        self.cleanup_state_file = self.root / "cleanup_state.json"
        self.index_dir.mkdir(parents=True, exist_ok=True)
        self.blob_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def put_file(
        self,
        key: str,
        source_path: str,
        namespace: Optional[str] = None,
        retention_days: int = 7,
        source_filename: Optional[str] = None,
    ) -> Dict[str, Any]:
        source = Path(source_path).expanduser().resolve(strict=True)
        if not source.is_file():
            raise ValueError(f"源路径不是文件: {source_path}")

        with self._lock:
            metadata, target = self._prepare_write(
                key,
                namespace,
                retention_days,
                desired_filename=source_filename or source.name,
                preserve_filename=True,
            )
            shutil.copyfile(source, target)
            return self._finish_write(metadata, target, namespace)

    def put_bytes(
        self,
        key: str,
        content: bytes,
        namespace: Optional[str] = None,
        retention_days: int = 7,
        default_suffix: str = "",
    ) -> Dict[str, Any]:
        with self._lock:
            metadata, target = self._prepare_write(
                key,
                namespace,
                retention_days,
                preserve_filename=False,
                default_suffix=default_suffix,
            )
            target.write_bytes(content)
            return self._finish_write(metadata, target, namespace)

    def get_path(self, key: str, namespace: Optional[str] = None) -> Optional[str]:
        with self._lock:
            item = self._load_index(namespace).get(key)
            if not item:
                return None
            if self._is_expired(item):
                return None
            path = self._path_from_metadata(item, namespace)
            if not path.is_file():
                return None
            return str(path)

    def get_metadata(
        self, key: str, namespace: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        with self._lock:
            item = self._load_index(namespace).get(key)
            if not item:
                return None
            if self._is_expired(item):
                return None
            return dict(item)

    def list_files(
        self, namespace: Optional[str] = None, prefix: str = ""
    ) -> List[Dict[str, Any]]:
        with self._lock:
            index = self._load_index(namespace)
            items = []
            for key, item in index.items():
                if prefix and not key.startswith(prefix):
                    continue
                if self._is_expired(item):
                    continue
                data = dict(item)
                data["exists"] = self._path_from_metadata(item, namespace).is_file()
                items.append(data)
            return sorted(items, key=lambda item: item.get("key", ""))

    def delete(self, key: str, namespace: Optional[str] = None) -> bool:
        with self._lock:
            index = self._load_index(namespace)
            item = index.pop(key, None)
            if not item:
                return False
            path = self._path_from_metadata(item, namespace)
            if path.exists():
                path.unlink()
            self._save_index(namespace, index)
            return True

    def cleanup_expired_once_per_day(self) -> int:
        today = datetime.now(timezone.utc).date().isoformat()
        with self._lock:
            state = self._load_cleanup_state()
            if state.get("last_cleanup_date") == today:
                return 0

            removed = 0
            for index_file in self.index_dir.glob("*.json"):
                namespace_id = index_file.stem
                index = self._load_index_by_file(index_file)
                changed = False
                for key, item in list(index.items()):
                    if not self._is_expired(item):
                        continue
                    path = self._path_from_metadata_by_namespace_id(
                        item, namespace_id
                    )
                    if path.exists():
                        try:
                            path.unlink()
                        except Exception as e:
                            logger.warning(f"[FileStorage] 删除过期文件失败: {e}")
                            continue
                    del index[key]
                    removed += 1
                    changed = True
                if changed:
                    self._save_index_file(index_file, index)

            self._save_cleanup_state(
                {"last_cleanup_date": today, "last_removed_count": removed}
            )
            return removed

    def _prepare_write(
        self,
        key: str,
        namespace: Optional[str],
        retention_days: int,
        desired_filename: Optional[str] = None,
        preserve_filename: bool = False,
        default_suffix: str = "",
    ) -> tuple[Dict[str, Any], Path]:
        if not key or not str(key).strip():
            raise ValueError("key 不能为空")

        index = self._load_index(namespace)
        existing = index.get(key, {})
        namespace_dir = self._namespace_blob_dir(namespace)
        namespace_dir.mkdir(parents=True, exist_ok=True)

        filename = _filename_from_key(key)
        suffix = Path(filename).suffix or default_suffix
        file_id = existing.get("file_id") or sha256(
            f"{namespace or 'global'}\0{key}".encode("utf-8")
        ).hexdigest()

        if preserve_filename:
            filename = _safe_filename(desired_filename or filename, filename)
            blob_name = self._unique_blob_name(
                namespace_dir, filename, existing.get("blob_name")
            )
        else:
            blob_name = f"{file_id}{suffix}"
        target = (namespace_dir / blob_name).resolve(strict=False)
        self._ensure_inside(target, namespace_dir)

        now = _utc_now()
        expires_at = None
        if retention_days != -1:
            expires_at = (
                datetime.now(timezone.utc) + timedelta(days=retention_days)
            ).isoformat(timespec="seconds")
        metadata = {
            "key": key,
            "file_id": file_id,
            "filename": filename,
            "blob_name": blob_name,
            "created_at": existing.get("created_at", now),
            "updated_at": now,
            "retention_days": retention_days,
            "expires_at": expires_at,
        }
        old_blob_name = existing.get("blob_name")
        if old_blob_name and old_blob_name != blob_name:
            metadata["_old_blob_name"] = old_blob_name
        return metadata, target

    def _finish_write(
        self,
        metadata: Dict[str, Any],
        target: Path,
        namespace: Optional[str],
    ) -> Dict[str, Any]:
        old_blob_name = metadata.pop("_old_blob_name", None)
        metadata["size"] = target.stat().st_size
        index = self._load_index(namespace)
        index[metadata["key"]] = metadata
        self._save_index(namespace, index)
        if old_blob_name:
            old_path = (self._namespace_blob_dir(namespace) / old_blob_name).resolve(
                strict=False
            )
            self._ensure_inside(old_path, self._namespace_blob_dir(namespace))
            if old_path.exists() and old_path != target:
                try:
                    old_path.unlink()
                except Exception as e:
                    logger.warning(f"[FileStorage] 删除旧文件失败: {e}")
        return dict(metadata)

    @staticmethod
    def _unique_blob_name(
        namespace_dir: Path, filename: str, existing_blob_name: Optional[str] = None
    ) -> str:
        safe_name = _safe_filename(filename, "file")
        if existing_blob_name == safe_name or not (namespace_dir / safe_name).exists():
            return safe_name

        path = Path(safe_name)
        stem = path.stem or "file"
        suffix = path.suffix
        for index in range(1, 10000):
            candidate = f"{stem}_{index}{suffix}"
            if existing_blob_name == candidate or not (namespace_dir / candidate).exists():
                return candidate
        digest = sha256(safe_name.encode("utf-8")).hexdigest()[:12]
        return f"{stem}_{digest}{suffix}"

    def _index_file(self, namespace: Optional[str]) -> Path:
        return self.index_dir / f"{_namespace_id(namespace)}.json"

    def _namespace_blob_dir(self, namespace: Optional[str]) -> Path:
        return self.blob_dir / _namespace_id(namespace)

    def _load_index(self, namespace: Optional[str]) -> Dict[str, Dict[str, Any]]:
        index_file = self._index_file(namespace)
        if not index_file.exists():
            return {}
        try:
            data = self._load_json_file(index_file)
            if isinstance(data, dict):
                return data
        except Exception as e:
            logger.error(f"[FileStorage] 加载索引失败: {e}")
        return {}

    def _save_index(
        self, namespace: Optional[str], data: Dict[str, Dict[str, Any]]
    ) -> None:
        index_file = self._index_file(namespace)
        self._save_index_file(index_file, data)

    def _load_index_by_file(self, index_file: Path) -> Dict[str, Dict[str, Any]]:
        data = self._load_json_file(index_file)
        if isinstance(data, dict):
            return data
        return {}

    def _save_index_file(
        self, index_file: Path, data: Dict[str, Dict[str, Any]]
    ) -> None:
        with open(index_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def _path_from_metadata(self, item: Dict[str, Any], namespace: Optional[str]) -> Path:
        namespace_dir = self._namespace_blob_dir(namespace)
        return self._path_from_metadata_in_dir(item, namespace_dir)

    def _path_from_metadata_by_namespace_id(
        self, item: Dict[str, Any], namespace_id: str
    ) -> Path:
        namespace_dir = self.blob_dir / namespace_id
        return self._path_from_metadata_in_dir(item, namespace_dir)

    def _path_from_metadata_in_dir(self, item: Dict[str, Any], namespace_dir: Path) -> Path:
        blob_name = item.get("blob_name") or item.get("file_id") or ""
        path = (namespace_dir / blob_name).resolve(strict=False)
        self._ensure_inside(path, namespace_dir)
        return path

    def _load_cleanup_state(self) -> Dict[str, Any]:
        data = self._load_json_file(self.cleanup_state_file)
        if isinstance(data, dict):
            return data
        return {}

    def _save_cleanup_state(self, data: Dict[str, Any]) -> None:
        with open(self.cleanup_state_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    @staticmethod
    def _load_json_file(path: Path) -> Any:
        if not path.exists():
            return {}
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    @staticmethod
    def _is_expired(item: Dict[str, Any]) -> bool:
        expires_at = _parse_utc(str(item.get("expires_at") or ""))
        if expires_at is None:
            return False
        return expires_at <= datetime.now(timezone.utc)

    @staticmethod
    def _ensure_inside(path: Path, root: Path) -> None:
        resolved_root = root.resolve(strict=False)
        try:
            path.relative_to(resolved_root)
        except ValueError:
            raise ValueError("文件路径越界")
