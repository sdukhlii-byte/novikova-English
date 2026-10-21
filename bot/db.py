from __future__ import annotations

import json
import os
import time
from typing import Any

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    username TEXT, first_name TEXT,
    source TEXT DEFAULT 'direct',
    first_seen INTEGER, last_seen INTEGER,
    quiz_started_at INTEGER, quiz_done_at INTEGER, lead_at INTEGER,
    muted INTEGER DEFAULT 0, blocked INTEGER DEFAULT 0,
    dropoff_reason TEXT, pause_until INTEGER,
    mini_started_at INTEGER, mini_done_at INTEGER
);
CREATE TABLE IF NOT EXISTS sessions (
    user_id INTEGER PRIMARY KEY,
    mode TEXT, step INTEGER DEFAULT 0, data TEXT DEFAULT '{}', updated_at INTEGER
);
CREATE TABLE IF NOT EXISTS quiz_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER, created_at INTEGER,
    age TEXT, goal TEXT, level TEXT, pain TEXT, fmt TEXT, time_pref TEXT
);
CREATE TABLE IF NOT EXISTS leads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER, created_at INTEGER,
    name TEXT, country TEXT, phone TEXT, username TEXT,
    quiz_id INTEGER, source TEXT,
    status TEXT DEFAULT 'new', trial_at INTEGER, note TEXT,
    card_chat_id INTEGER, card_message_id INTEGER,
    followup_kind TEXT
);
CREATE TABLE IF NOT EXISTS followups (
    user_id INTEGER, kind TEXT, sent_at INTEGER,
    variant TEXT, delivered INTEGER DEFAULT 0,
    PRIMARY KEY (user_id, kind)
);
CREATE TABLE IF NOT EXISTS relay (
    chat_id INTEGER, message_id INTEGER, user_id INTEGER,
    PRIMARY KEY (chat_id, message_id)
);
CREATE INDEX IF NOT EXISTS idx_users_first_seen ON users(first_seen);
CREATE INDEX IF NOT EXISTS idx_leads_user ON leads(user_id);
"""


def now() -> int:
    return int(time.time())


class DB:
    def __init__(self, path: str):
        self.path = path
        self.conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        folder = os.path.dirname(self.path)
        if folder:
            os.makedirs(folder, exist_ok=True)
        self.conn = await aiosqlite.connect(self.path)
        self.conn.row_factory = aiosqlite.Row
        await self.conn.execute("PRAGMA journal_mode=WAL")
        await self.conn.executescript(SCHEMA)
        await self._migrate()
        await self.conn.commit()

    async def _migrate(self) -> None:
        """Добавляет колонки, которых нет в базе, созданной предыдущей версией бота."""
        wanted = {
            "users": [("dropoff_reason", "TEXT"), ("pause_until", "INTEGER"),
                      ("mini_started_at", "INTEGER"), ("mini_done_at", "INTEGER")],
            "leads": [("followup_kind", "TEXT")],
            "followups": [("variant", "TEXT"), ("delivered", "INTEGER DEFAULT 0")],
        }
        for table, cols in wanted.items():
            have = {r["name"] for r in await self._all(f"PRAGMA table_info({table})")}
            for name, ddl in cols:
                if name not in have:
                    await self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")

    async def close(self) -> None:
        if self.conn:
            await self.conn.close()

    # ── low level ──
    async def _exec(self, sql: str, params: tuple = ()) -> int:
        cur = await self.conn.execute(sql, params)
        await self.conn.commit()
        return cur.lastrowid

    async def _one(self, sql: str, params: tuple = ()) -> dict | None:
        cur = await self.conn.execute(sql, params)
        row = await cur.fetchone()
        return dict(row) if row else None

    async def _all(self, sql: str, params: tuple = ()) -> list[dict]:
        cur = await self.conn.execute(sql, params)
        return [dict(r) for r in await cur.fetchall()]

    async def _update(self, table: str, key: str, value: Any, **fields: Any) -> None:
        if not fields:
            return
        cols = ", ".join(f"{k}=?" for k in fields)
        await self._exec(f"UPDATE {table} SET {cols} WHERE {key}=?", (*fields.values(), value))

    # ── users ──
    async def touch_user(self, user) -> None:
        t = now()
        await self._exec(
            """INSERT INTO users (user_id, username, first_name, first_seen, last_seen)
               VALUES (?,?,?,?,?)
               ON CONFLICT(user_id) DO UPDATE SET
                 username=excluded.username, first_name=excluded.first_name, last_seen=excluded.last_seen""",
            (user.id, user.username, user.first_name, t, t),
        )

    async def set_source(self, user_id: int, source: str) -> None:
        """Источник фиксируется по первому касанию с меткой (organic 'direct' можно перезаписать)."""
        await self._exec(
            "UPDATE users SET source=? WHERE user_id=? AND (source IS NULL OR source='direct')",
            (source, user_id),
        )

    async def get_user(self, user_id: int) -> dict | None:
        return await self._one("SELECT * FROM users WHERE user_id=?", (user_id,))

    async def update_user(self, user_id: int, **fields: Any) -> None:
        await self._update("users", "user_id", user_id, **fields)

    # ── sessions (состояние диалога хранится в БД, переживает рестарт) ──
    async def get_session(self, user_id: int) -> dict | None:
        row = await self._one("SELECT * FROM sessions WHERE user_id=?", (user_id,))
        if not row:
            return None
        row["data"] = json.loads(row["data"] or "{}")
        return row

    async def set_session(self, user_id: int, mode: str, step: int = 0, data: dict | None = None) -> None:
        await self._exec(
            """INSERT INTO sessions (user_id, mode, step, data, updated_at) VALUES (?,?,?,?,?)
               ON CONFLICT(user_id) DO UPDATE SET
                 mode=excluded.mode, step=excluded.step, data=excluded.data, updated_at=excluded.updated_at""",
            (user_id, mode, step, json.dumps(data or {}, ensure_ascii=False), now()),
        )

    async def clear_session(self, user_id: int) -> None:
        await self._exec("DELETE FROM sessions WHERE user_id=?", (user_id,))

    # ── quiz ──
    async def save_quiz(self, user_id: int, a: dict) -> int:
        return await self._exec(
            """INSERT INTO quiz_results (user_id, created_at, age, goal, level, pain, fmt, time_pref)
               VALUES (?,?,?,?,?,?,?,?)""",
            (user_id, now(), a.get("age"), a.get("goal"), a.get("level"), a.get("pain"), a.get("fmt"), a.get("time")),
        )

    async def get_quiz(self, quiz_id: int | None) -> dict | None:
        if not quiz_id:
            return None
        return await self._one("SELECT * FROM quiz_results WHERE id=?", (quiz_id,))

    async def latest_quiz(self, user_id: int) -> dict | None:
        return await self._one(
            "SELECT * FROM quiz_results WHERE user_id=? ORDER BY id DESC LIMIT 1", (user_id,)
        )

    # ── leads ──
    async def create_lead(self, **f: Any) -> int:
        f.setdefault("created_at", now())
        cols = ", ".join(f)
        marks = ", ".join("?" for _ in f)
        return await self._exec(f"INSERT INTO leads ({cols}) VALUES ({marks})", tuple(f.values()))

    async def get_lead(self, lead_id: int) -> dict | None:
        return await self._one("SELECT * FROM leads WHERE id=?", (lead_id,))

    async def latest_lead(self, user_id: int) -> dict | None:
        return await self._one(
            "SELECT * FROM leads WHERE user_id=? ORDER BY id DESC LIMIT 1", (user_id,)
        )

    async def update_lead(self, lead_id: int, **fields: Any) -> None:
        await self._update("leads", "id", lead_id, **fields)

    async def list_leads(self, limit: int = 10) -> list[dict]:
        return await self._all("SELECT * FROM leads ORDER BY id DESC LIMIT ?", (limit,))

    async def export_rows(self) -> list[dict]:
        return await self._all(
            """SELECT l.id, l.created_at, l.status, l.name, l.country, l.phone, l.username,
                      q.age, q.goal, q.level, q.pain, q.fmt, q.time_pref,
                      l.source, l.trial_at, l.note
               FROM leads l LEFT JOIN quiz_results q ON q.id = l.quiz_id ORDER BY l.id"""
        )

    # ── статистика воронки (когорта по дате первого /start) ──
    async def funnel(self, since: int) -> list[dict]:
        base = await self._all(
            """SELECT COALESCE(source,'direct') AS source,
                      COUNT(*) AS starts,
                      COALESCE(SUM(mini_started_at IS NOT NULL),0) AS mini_started,
                      COALESCE(SUM(mini_done_at IS NOT NULL),0) AS mini_done,
                      COALESCE(SUM(quiz_started_at IS NOT NULL),0) AS quiz_started,
                      COALESCE(SUM(quiz_done_at IS NOT NULL),0) AS quiz_done,
                      COALESCE(SUM(lead_at IS NOT NULL),0) AS leads
               FROM users WHERE first_seen>=? GROUP BY 1 ORDER BY starts DESC""",
            (since,),
        )
        deep = await self._all(
            """SELECT COALESCE(u.source,'direct') AS source,
                      COALESCE(SUM(l.status IN ('trial','paid')),0) AS trial,
                      COALESCE(SUM(l.status='paid'),0) AS paid
               FROM leads l JOIN users u ON u.user_id=l.user_id
               WHERE u.first_seen>=? GROUP BY 1""",
            (since,),
        )
        extra = {d["source"]: d for d in deep}
        for row in base:
            row["trial"] = extra.get(row["source"], {}).get("trial", 0)
            row["paid"] = extra.get(row["source"], {}).get("paid", 0)
        return base

    # ── догрев ──
    async def sent_kinds(self, user_id: int) -> set[str]:
        rows = await self._all("SELECT kind FROM followups WHERE user_id=?", (user_id,))
        return {r["kind"] for r in rows}

    async def mark_followups(self, user_id: int, kinds: list[str]) -> None:
        """Отмечает шаги как «пройденные» (в т.ч. пропущенные), чтобы не слать их повторно."""
        t = now()
        for k in kinds:
            await self._exec(
                "INSERT OR IGNORE INTO followups (user_id, kind, sent_at) VALUES (?,?,?)", (user_id, k, t)
            )

    async def mark_delivered(self, user_id: int, kind: str, variant: str) -> None:
        """Шаг реально отправлен: запоминаем трек (a/b), чтобы считать конверсию."""
        await self._exec(
            "INSERT OR REPLACE INTO followups (user_id, kind, sent_at, variant, delivered) VALUES (?,?,?,?,1)",
            (user_id, kind, now(), variant),
        )

    async def last_followup(self, user_id: int, within_s: int = 36 * 3600) -> str | None:
        """Последнее доставленное напоминание догрева (f*/u*) за окно: «f2:a». Для атрибуции заявки."""
        row = await self._one(
            """SELECT kind, variant FROM followups
               WHERE user_id=? AND delivered=1 AND (kind LIKE 'f%' OR kind LIKE 'u%' OR kind LIKE 'm%' OR kind LIKE 'w%') AND sent_at>=?
               ORDER BY sent_at DESC, rowid DESC LIMIT 1""",
            (user_id, now() - within_s),
        )
        return f"{row['kind']}:{row['variant']}" if row else None

    async def quiz_done_candidates(self, min_ts: int, now_ts: int) -> list[dict]:
        return await self._all(
            """SELECT u.user_id, u.first_name, u.quiz_done_at AS ts, q.age, q.goal, q.pain
               FROM users u LEFT JOIN quiz_results q ON q.id =
                    (SELECT MAX(id) FROM quiz_results WHERE user_id=u.user_id)
               WHERE u.muted=0 AND u.blocked=0 AND u.quiz_done_at IS NOT NULL
                 AND u.lead_at IS NULL AND u.quiz_done_at>=?
                 AND (u.pause_until IS NULL OR u.pause_until<?)""",
            (min_ts, now_ts),
        )

    async def unfinished_candidates(self, min_ts: int, now_ts: int) -> list[dict]:
        return await self._all(
            """SELECT user_id, first_name, quiz_started_at AS ts, NULL AS age, NULL AS goal, NULL AS pain
               FROM users
               WHERE muted=0 AND blocked=0 AND quiz_started_at IS NOT NULL
                 AND (quiz_done_at IS NULL OR quiz_done_at < quiz_started_at)
                 AND lead_at IS NULL AND quiz_started_at>=?
                 AND (pause_until IS NULL OR pause_until<?)""",
            (min_ts, now_ts),
        )

    async def mark_mini(self, user_id: int, field: str) -> None:
        """Отмечает, что человек начал (mini_started_at) / прошёл (mini_done_at) мини-проверку; первое время сохраняется."""
        assert field in ("mini_started_at", "mini_done_at")
        await self._exec(f"UPDATE users SET {field}=COALESCE({field}, ?) WHERE user_id=?", (now(), user_id))

    async def mini_candidates(self, min_ts: int, now_ts: int) -> list[dict]:
        """Сыграли в мини-проверку, но не начали квиз и не оставили заявку."""
        return await self._all(
            """SELECT user_id, first_name, COALESCE(mini_done_at, mini_started_at) AS ts,
                      NULL AS age, NULL AS goal, NULL AS pain
               FROM users
               WHERE muted=0 AND blocked=0 AND mini_started_at IS NOT NULL
                 AND quiz_started_at IS NULL AND lead_at IS NULL
                 AND COALESCE(mini_done_at, mini_started_at)>=?
                 AND (pause_until IS NULL OR pause_until<?)
                 AND user_id NOT IN (SELECT user_id FROM sessions WHERE mode='lead' AND updated_at>?)""",
            (min_ts, now_ts, now_ts - 6 * 3600),
        )

    async def idle_candidates(self, min_ts: int, now_ts: int) -> list[dict]:
        """Открыли бота, но не сделали ничего: ни мини-проверки, ни квиза, ни заявки."""
        return await self._all(
            """SELECT user_id, first_name, first_seen AS ts, NULL AS age, NULL AS goal, NULL AS pain
               FROM users
               WHERE muted=0 AND blocked=0 AND mini_started_at IS NULL
                 AND quiz_started_at IS NULL AND lead_at IS NULL AND first_seen>=?
                 AND (pause_until IS NULL OR pause_until<?)
                 AND user_id NOT IN (SELECT user_id FROM sessions WHERE mode='lead' AND updated_at>?)""",
            (min_ts, now_ts, now_ts - 6 * 3600),
        )

    async def followup_stats(self, since: int) -> dict:
        """Что отправлено и сколько заявок принесло каждое сообщение / каждый трек."""
        kinds = "(kind LIKE 'f%' OR kind LIKE 'u%' OR kind LIKE 'm%' OR kind LIKE 'w%')"
        delivered = await self._all(
            f"""SELECT kind, variant, COUNT(*) AS n FROM followups
                WHERE delivered=1 AND sent_at>=? AND {kinds} GROUP BY 1,2 ORDER BY 1,2""",
            (since,),
        )
        by_kind = {
            r["followup_kind"]: r["n"]
            for r in await self._all(
                """SELECT followup_kind, COUNT(*) AS n FROM leads
                   WHERE created_at>=? AND followup_kind IS NOT NULL GROUP BY 1""",
                (since,),
            )
        }
        tracks = await self._all(
            f"""SELECT variant, COUNT(DISTINCT user_id) AS users FROM followups
                WHERE delivered=1 AND sent_at>=? AND {kinds} GROUP BY 1 ORDER BY 1""",
            (since,),
        )
        organic = await self._one(
            "SELECT COUNT(*) AS n FROM leads WHERE created_at>=? AND followup_kind IS NULL", (since,)
        )
        reasons = await self._all(
            """SELECT dropoff_reason AS reason, COUNT(*) AS n FROM users
               WHERE dropoff_reason IS NOT NULL AND last_seen>=? GROUP BY 1 ORDER BY 2 DESC""",
            (since,),
        )
        return {
            "delivered": delivered, "leads_by_kind": by_kind, "tracks": tracks,
            "organic_leads": organic["n"] if organic else 0, "reasons": reasons,
        }

    async def upcoming_trials(self, now_ts: int) -> list[dict]:
        return await self._all(
            """SELECT l.id, l.user_id, l.trial_at, l.note, l.name, l.country FROM leads l
               JOIN users u ON u.user_id=l.user_id
               WHERE l.status='trial' AND l.trial_at IS NOT NULL AND l.trial_at>? AND u.blocked=0""",
            (now_ts,),
        )

    # ── relay (ответы преподавателя клиенту через бота) ──
    async def relay_put(self, chat_id: int, message_id: int, user_id: int) -> None:
        await self._exec(
            "INSERT OR REPLACE INTO relay (chat_id, message_id, user_id) VALUES (?,?,?)",
            (chat_id, message_id, user_id),
        )

    async def relay_get(self, chat_id: int, message_id: int) -> int | None:
        row = await self._one(
            "SELECT user_id FROM relay WHERE chat_id=? AND message_id=?", (chat_id, message_id)
        )
        return row["user_id"] if row else None

    # ── удаление данных пользователя (GDPR) ──
    async def delete_user_data(self, user_id: int) -> list[int]:
        leads = await self._all("SELECT id FROM leads WHERE user_id=?", (user_id,))
        for table in ("sessions", "quiz_results", "leads", "followups", "relay", "users"):
            await self._exec(f"DELETE FROM {table} WHERE user_id=?", (user_id,))
        return [l["id"] for l in leads]
