import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from astrbot.api import logger
from ...core import StorageBackend


def _safe_store_name(store_name: str) -> str:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in store_name)
    return safe or "kvstore"


class JSONStorageBackend(StorageBackend):
    """JSON 文件存储后端"""

    def __init__(self, data_dir: str, store_name: str = "kvstore"):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.store_name = _safe_store_name(store_name)
        self.global_file = self.data_dir / f"{self.store_name}_global.json"
        self.namespace_files: Dict[str, Path] = {}
        self._global_data: Dict[str, Any] = {}
        self._lock = threading.Lock()
        self._load_global()

    def _load_global(self) -> None:
        if self.global_file.exists():
            try:
                with open(self.global_file, encoding="utf-8") as f:
                    self._global_data = json.load(f)
            except Exception as e:
                logger.error(f"[JSONStorage] 加载全局数据失败: {e}")
                self._global_data = {}

    def _save_global(self) -> None:
        try:
            with open(self.global_file, "w", encoding="utf-8") as f:
                json.dump(self._global_data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"[JSONStorage] 保存全局数据失败: {e}")

    def _get_namespace_file(self, namespace: str) -> Path:
        if namespace not in self.namespace_files:
            safe_ns = "".join(
                c if c.isalnum() or c in "-_" else "_" for c in namespace
            )
            self.namespace_files[namespace] = (
                self.data_dir / f"{self.store_name}_ns_{safe_ns}.json"
            )
        return self.namespace_files[namespace]

    def _load_namespace(self, namespace: str) -> Dict[str, Any]:
        ns_file = self._get_namespace_file(namespace)
        if ns_file.exists():
            try:
                with open(ns_file, encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"[JSONStorage] 加载命名空间 {namespace} 失败: {e}")
        return {}

    def _save_namespace(self, namespace: str, data: Dict[str, Any]) -> None:
        ns_file = self._get_namespace_file(namespace)
        try:
            with open(ns_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"[JSONStorage] 保存命名空间 {namespace} 失败: {e}")

    def get(self, key: str, namespace: Optional[str] = None) -> Optional[Any]:
        with self._lock:
            if namespace:
                return self._load_namespace(namespace).get(key)
            return self._global_data.get(key)

    def set(self, key: str, value: Any, namespace: Optional[str] = None) -> None:
        with self._lock:
            if namespace:
                data = self._load_namespace(namespace)
                data[key] = value
                self._save_namespace(namespace, data)
            else:
                self._global_data[key] = value
                self._save_global()

    def delete(self, key: str, namespace: Optional[str] = None) -> bool:
        with self._lock:
            if namespace:
                data = self._load_namespace(namespace)
                if key in data:
                    del data[key]
                    self._save_namespace(namespace, data)
                    return True
                return False
            if key in self._global_data:
                del self._global_data[key]
                self._save_global()
                return True
            return False

    def list_keys(self, namespace: Optional[str] = None) -> List[str]:
        with self._lock:
            if namespace:
                return list(self._load_namespace(namespace).keys())
            return list(self._global_data.keys())

    def search(
        self, keyword: str, namespace: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        results = []
        keys = self.list_keys(namespace)
        for key in keys:
            if keyword.lower() in key.lower():
                value = self.get(key, namespace)
                results.append({"key": key, "value": value})
        return results

    def clear_namespace(self, namespace: str) -> None:
        with self._lock:
            ns_file = self._get_namespace_file(namespace)
            if ns_file.exists():
                ns_file.unlink()
            if namespace in self.namespace_files:
                del self.namespace_files[namespace]


class SQLiteStorageBackend(StorageBackend):
    """SQLite 数据库存储后端"""

    def __init__(self, data_dir: str, store_name: str = "kvstore"):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.store_name = _safe_store_name(store_name)
        self.db_file = self.data_dir / f"{self.store_name}.db"
        self._lock = threading.Lock()
        self._init_db()

    def _get_connection(self):
        return sqlite3.connect(str(self.db_file), check_same_thread=False)

    def _init_db(self) -> None:
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS kvstore (
                key TEXT NOT NULL,
                value TEXT NOT NULL,
                namespace TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (key, namespace)
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_namespace ON kvstore(namespace)")
        conn.commit()
        conn.close()

    def get(self, key: str, namespace: Optional[str] = None) -> Optional[Any]:
        with self._lock:
            conn = self._get_connection()
            cursor = conn.cursor()
            if namespace:
                cursor.execute(
                    "SELECT value FROM kvstore WHERE key = ? AND namespace = ?",
                    (key, namespace),
                )
            else:
                cursor.execute(
                    "SELECT value FROM kvstore WHERE key = ? AND namespace IS NULL",
                    (key,),
                )
            row = cursor.fetchone()
            conn.close()
            if row:
                try:
                    return json.loads(row[0])
                except Exception:
                    return row[0]
            return None

    def set(self, key: str, value: Any, namespace: Optional[str] = None) -> None:
        with self._lock:
            conn = self._get_connection()
            cursor = conn.cursor()
            value_str = json.dumps(value, ensure_ascii=False)
            if namespace:
                cursor.execute(
                    "INSERT OR REPLACE INTO kvstore (key, value, namespace, updated_at) "
                    "VALUES (?, ?, ?, CURRENT_TIMESTAMP)",
                    (key, value_str, namespace),
                )
            else:
                cursor.execute(
                    "INSERT OR REPLACE INTO kvstore (key, value, namespace, updated_at) "
                    "VALUES (?, ?, NULL, CURRENT_TIMESTAMP)",
                    (key, value_str),
                )
            conn.commit()
            conn.close()

    def delete(self, key: str, namespace: Optional[str] = None) -> bool:
        with self._lock:
            conn = self._get_connection()
            cursor = conn.cursor()
            if namespace:
                cursor.execute(
                    "DELETE FROM kvstore WHERE key = ? AND namespace = ?",
                    (key, namespace),
                )
            else:
                cursor.execute(
                    "DELETE FROM kvstore WHERE key = ? AND namespace IS NULL",
                    (key,),
                )
            affected = cursor.rowcount
            conn.commit()
            conn.close()
            return affected > 0

    def list_keys(self, namespace: Optional[str] = None) -> List[str]:
        with self._lock:
            conn = self._get_connection()
            cursor = conn.cursor()
            if namespace:
                cursor.execute(
                    "SELECT key FROM kvstore WHERE namespace = ?", (namespace,)
                )
            else:
                cursor.execute("SELECT key FROM kvstore WHERE namespace IS NULL")
            rows = cursor.fetchall()
            conn.close()
            return [row[0] for row in rows]

    def search(
        self, keyword: str, namespace: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        results = []
        keys = self.list_keys(namespace)
        for key in keys:
            if keyword.lower() in key.lower():
                value = self.get(key, namespace)
                results.append({"key": key, "value": value})
        return results

    def clear_namespace(self, namespace: str) -> None:
        with self._lock:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute("DELETE FROM kvstore WHERE namespace = ?", (namespace,))
            conn.commit()
            conn.close()


def create_storage_backend(
    data_dir: str, use_sqlite: bool = False, store_name: str = "kvstore"
) -> StorageBackend:
    """工厂函数：创建存储后端"""
    if use_sqlite:
        return SQLiteStorageBackend(data_dir, store_name=store_name)
    return JSONStorageBackend(data_dir, store_name=store_name)
