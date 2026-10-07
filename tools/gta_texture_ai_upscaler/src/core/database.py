from __future__ import annotations

"""
SQLite job queue for resilient batch processing.
Supports resume after crash.
"""

import hashlib
import logging
import sqlite3
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import List, Optional, Tuple

from .config import AppConfig

logger = logging.getLogger("database")

class JobStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


class JobDatabase:
    def __init__(self, cfg: AppConfig):
        self.cfg = cfg
        self.db_path = cfg.resolve_path(cfg.database.path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: Optional[sqlite3.Connection] = None
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL;")
            self._conn.execute("PRAGMA synchronous=NORMAL;")
        return self._conn

    def _init_schema(self) -> None:
        conn = self._connect()
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                source_path     TEXT NOT NULL UNIQUE,
                relative_path   TEXT NOT NULL,
                output_path     TEXT,
                status          TEXT NOT NULL DEFAULT 'PENDING',
                seed            INTEGER,
                attempts        INTEGER NOT NULL DEFAULT 0,
                last_error      TEXT,
                created_at      TEXT NOT NULL,
                started_at      TEXT,
                finished_at     TEXT,
                processing_ms   INTEGER
            );

            CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
            CREATE INDEX IF NOT EXISTS idx_jobs_source ON jobs(source_path);
            """
        )
        conn.commit()
        logger.info(f"Database ready: {self.db_path}")

    @staticmethod
    def make_seed(source_path: str) -> int:
        """Deterministic seed from file path (SHA256 → int32)."""
        h = hashlib.sha256(source_path.encode("utf-8")).hexdigest()
        return int(h[:8], 16) % (2**31)

    def add_job(self, source_path: Path, relative_path: str, output_path: Path) -> bool:
        """Insert a new PENDING job. Returns False if already exists."""
        conn = self._connect()
        now = datetime.utcnow().isoformat()
        seed = self.make_seed(str(source_path))
        try:
            conn.execute(
                """
                INSERT INTO jobs (source_path, relative_path, output_path, status, seed, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (str(source_path), relative_path, str(output_path), JobStatus.PENDING.value, seed, now),
            )
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def get_next_pending(self) -> Optional[sqlite3.Row]:
        conn = self._connect()
        row = conn.execute(
            "SELECT * FROM jobs WHERE status = ? ORDER BY id ASC LIMIT 1",
            (JobStatus.PENDING.value,),
        ).fetchone()
        return row

    def mark_running(self, job_id: int) -> None:
        conn = self._connect()
        now = datetime.utcnow().isoformat()
        conn.execute(
            "UPDATE jobs SET status = ?, started_at = ?, attempts = attempts + 1 WHERE id = ?",
            (JobStatus.RUNNING.value, now, job_id),
        )
        conn.commit()

    def mark_completed(self, job_id: int, processing_ms: int) -> None:
        conn = self._connect()
        now = datetime.utcnow().isoformat()
        conn.execute(
            """
            UPDATE jobs SET status = ?, finished_at = ?, processing_ms = ?, last_error = NULL
            WHERE id = ?
            """,
            (JobStatus.COMPLETED.value, now, processing_ms, job_id),
        )
        conn.commit()

    def mark_failed(self, job_id: int, error: str, max_retries: int) -> None:
        conn = self._connect()
        now = datetime.utcnow().isoformat()
        row = conn.execute("SELECT attempts FROM jobs WHERE id = ?", (job_id,)).fetchone()
        attempts = row["attempts"] if row else 0
        new_status = JobStatus.FAILED.value if attempts >= max_retries else JobStatus.PENDING.value
        conn.execute(
            """
            UPDATE jobs SET status = ?, finished_at = ?, last_error = ?
            WHERE id = ?
            """,
            (new_status, now, error[:2000], job_id),
        )
        conn.commit()


    def mark_skipped(self, job_id: int, reason: str = "") -> None:
        conn = self._connect()
        now = datetime.utcnow().isoformat()
        conn.execute(
            """
            UPDATE jobs SET status = ?, finished_at = ?, last_error = ?, processing_ms = 0
            WHERE id = ?
            """,
            (JobStatus.SKIPPED.value, now, (reason or "skipped")[:2000], job_id),
        )
        conn.commit()

    def list_skipped(self, limit: int = 5000):
        conn = self._connect()
        rows = conn.execute(
            "SELECT id, source_path, relative_path, output_path, last_error FROM jobs WHERE status = ? ORDER BY id ASC LIMIT ?",
            (JobStatus.SKIPPED.value, limit),
        ).fetchall()
        return rows

    def export_skipped_paths(self, out_file: Path) -> int:
        rows = self.list_skipped()
        out_file = Path(out_file)
        out_file.parent.mkdir(parents=True, exist_ok=True)
        with open(out_file, "w", encoding="utf-8") as f:
            for r in rows:
                f.write(r["source_path"] + "\n")
        return len(rows)


    def sync_fixed_folder_to_skipped(self, fixed_dir: Path, reason: str = "already in fixed-with-ai folder") -> int:
        """
        Fast scan of fixed_dir (recursive). Any PENDING job whose name or stem
        matches a file there is marked SKIPPED so Flux will not re-upscale it.
        Returns number of jobs newly skipped.
        """
        fixed_dir = Path(fixed_dir)
        if not fixed_dir.is_dir():
            logger.warning(f"fixed-with-ai folder not found: {fixed_dir}")
            return 0

        img_ext = {".png", ".jpg", ".jpeg", ".bmp", ".tga", ".webp", ".dds"}
        stems: set[str] = set()
        names: set[str] = set()
        try:
            # recursive — user may put files in subfolders
            for fp in fixed_dir.rglob("*"):
                if not fp.is_file():
                    continue
                if fp.suffix.lower() not in img_ext and fp.suffix:
                    # still index by name for extensionless dumps
                    pass
                names.add(fp.name.lower())
                stems.add(fp.stem.lower())
        except OSError as e:
            logger.warning(f"fixed-with-ai scan failed: {e}")
            return 0

        logger.info(f"fixed-with-ai scan: {len(names)} file(s) under {fixed_dir}")
        if not stems and not names:
            return 0

        conn = self._connect()
        rows = conn.execute(
            "SELECT id, source_path, relative_path, output_path FROM jobs WHERE status = ?",
            (JobStatus.PENDING.value,),
        ).fetchall()
        now = datetime.utcnow().isoformat()
        n = 0
        for row in rows:
            candidates: list[str] = []
            for key in ("source_path", "relative_path", "output_path"):
                val = row[key] if key in row.keys() else None
                if not val:
                    continue
                try:
                    pp = Path(val)
                    candidates.append(pp.name.lower())
                    candidates.append(pp.stem.lower())
                except Exception:
                    continue
            hit = False
            for c in candidates:
                if c in names or c in stems:
                    hit = True
                    break
            if not hit:
                continue
            conn.execute(
                """
                UPDATE jobs SET status = ?, finished_at = ?, last_error = ?, processing_ms = 0
                WHERE id = ? AND status = ?
                """,
                (JobStatus.SKIPPED.value, now, reason[:2000], row["id"], JobStatus.PENDING.value),
            )
            n += 1
        conn.commit()
        if n:
            logger.info(f"fixed-with-ai sync: marked {n} PENDING → SKIPPED")
        return n

    def recover_running_jobs(self) -> int:
        """After crash: any RUNNING jobs become PENDING again."""
        conn = self._connect()
        cur = conn.execute(
            "UPDATE jobs SET status = ? WHERE status = ?",
            (JobStatus.PENDING.value, JobStatus.RUNNING.value),
        )
        conn.commit()
        count = cur.rowcount
        if count:
            logger.warning(f"Recovered {count} interrupted RUNNING job(s) → PENDING")
        return count

    def stats(self) -> dict:
        conn = self._connect()
        rows = conn.execute(
            "SELECT status, COUNT(*) as cnt FROM jobs GROUP BY status"
        ).fetchall()
        result = {s.value: 0 for s in JobStatus}
        for r in rows:
            result[r["status"]] = r["cnt"]
        result["total"] = sum(result.values())
        return result

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None
