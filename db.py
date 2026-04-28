import os
import re
import sqlite3
from datetime import datetime
from pathlib import Path

DB_PATH = Path(os.environ.get("DATA_DIR", Path(__file__).parent)) / "history.db"


def init_db() -> None:
    with _connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS analyses (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT    NOT NULL DEFAULT '',
                source     TEXT    NOT NULL,
                timestamp  TEXT    NOT NULL,
                duration_s REAL,
                report_md  TEXT,
                critical   INTEGER DEFAULT 0,
                high       INTEGER DEFAULT 0,
                medium     INTEGER DEFAULT 0,
                low        INTEGER DEFAULT 0
            )
        """)
        # Migrate existing databases that predate the session_id column
        cols = {row[1] for row in conn.execute("PRAGMA table_info(analyses)")}
        if "session_id" not in cols:
            conn.execute(
                "ALTER TABLE analyses ADD COLUMN session_id TEXT NOT NULL DEFAULT ''"
            )


def save_analysis(
    source: str, report_md: str, duration_s: float, session_id: str = ""
) -> int:
    counts = _count_severity(report_md)
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO analyses "
            "(session_id, source, timestamp, duration_s, report_md, critical, high, medium, low) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (
                session_id,
                source,
                datetime.utcnow().isoformat(timespec="seconds"),
                round(duration_s),
                report_md,
                counts["critical"],
                counts["high"],
                counts["medium"],
                counts["low"],
            ),
        )
        return cur.lastrowid


def get_history(session_id: str = "") -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, source, timestamp, duration_s, critical, high, medium, low "
            "FROM analyses WHERE session_id = ? ORDER BY timestamp DESC LIMIT 100",
            (session_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_report(analysis_id: int, session_id: str = "") -> str:
    with _connect() as conn:
        row = conn.execute(
            "SELECT report_md FROM analyses WHERE id = ? AND session_id = ?",
            (analysis_id, session_id),
        ).fetchone()
    return row["report_md"] if row else "Report not found."


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _count_severity(report_md: str) -> dict:
    if not report_md:
        return {"critical": 0, "high": 0, "medium": 0, "low": 0}

    def find(label: str) -> int:
        return len(re.findall(rf"severity[*:\s]+{label}\b", report_md, re.IGNORECASE))

    return {
        "critical": find("critical"),
        "high": find("high"),
        "medium": find("medium"),
        "low": find("low"),
    }
