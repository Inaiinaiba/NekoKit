import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from astrbot.api import logger
from ..core import StorageBackend


class SQLiteStorageBackend(StorageBackend):
    """SQLite 数据库存储后端"""
    
    def __init__(self, data_dir: str):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db_file = self.data_dir / "kvstore.db"
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
                    "DELETE FROM kvstore WHERE key = ? AND namespace IS NULL", (key,)
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
                cursor.execute("SELECT key FROM kvstore WHERE namespace = ?", (namespace,))
            else:
                cursor.execute("SELECT key FROM kvstore WHERE namespace IS NULL")
            rows = cursor.fetchall()
            conn.close()
            return [row[0] for row in rows]
    
    def search(self, keyword: str, namespace: Optional[str] = None) -> List[Dict[str, Any]]:
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


def create_storage_backend(data_dir: str) -> StorageBackend:
    """工厂函数：创建存储后端，统一使用 SQLite"""
    return SQLiteStorageBackend(data_dir)