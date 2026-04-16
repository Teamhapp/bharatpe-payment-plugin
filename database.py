"""PostgreSQL database layer.

The active database URL is set once via init_db(db_url) and shared across
all functions via the module-level _db_url variable. Call init_db(cfg.db_url)
at startup before any other database function.
"""

import logging
import os
import psycopg2
from psycopg2.extras import RealDictCursor
from contextlib import contextmanager
from datetime import datetime

log = logging.getLogger(__name__)

_db_url: str = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/pay0_bot")

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id              SERIAL PRIMARY KEY,
    chat_id         BIGINT UNIQUE NOT NULL,
    username        VARCHAR(128),
    first_name      VARCHAR(128),
    is_blocked      BOOLEAN NOT NULL DEFAULT FALSE,
    is_verified     BOOLEAN NOT NULL DEFAULT FALSE,
    total_paid      NUMERIC(14,2) NOT NULL DEFAULT 0,
    payment_count   INT NOT NULL DEFAULT 0,
    failed_count    INT NOT NULL DEFAULT 0,
    first_seen      TIMESTAMP NOT NULL DEFAULT NOW(),
    last_seen       TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS payments (
    id              SERIAL PRIMARY KEY,
    order_id        VARCHAR(64) UNIQUE NOT NULL,
    user_id         BIGINT NOT NULL,
    base_amount     NUMERIC(12,2) NOT NULL,
    session_amount  NUMERIC(12,2) NOT NULL,
    status          VARCHAR(16) NOT NULL DEFAULT 'PENDING',
    utr             VARCHAR(64),
    payer_vpa       VARCHAR(128),
    created_at      TIMESTAMP NOT NULL DEFAULT NOW(),
    expire_at       TIMESTAMP NOT NULL,
    completed_at    TIMESTAMP,
    message_id      BIGINT
);

CREATE INDEX IF NOT EXISTS idx_pay_status ON payments(status);
CREATE INDEX IF NOT EXISTS idx_pay_user ON payments(user_id);
CREATE INDEX IF NOT EXISTS idx_pay_amount ON payments(session_amount) WHERE status = 'PENDING';
CREATE INDEX IF NOT EXISTS idx_pay_created ON payments(created_at);

CREATE TABLE IF NOT EXISTS activity_log (
    id              SERIAL PRIMARY KEY,
    user_id         BIGINT,
    action          VARCHAR(64) NOT NULL,
    detail          TEXT,
    created_at      TIMESTAMP NOT NULL DEFAULT NOW()
);
"""

# Idempotent migrations — run on every startup, safe to repeat
_MIGRATIONS = """
DO $$
BEGIN
    -- Drop any FK constraints on payments (legacy chat_id FK to users)
    DECLARE r RECORD;
    BEGIN
        FOR r IN
            SELECT constraint_name FROM information_schema.table_constraints
            WHERE table_name = 'payments' AND constraint_type = 'FOREIGN KEY'
        LOOP
            EXECUTE 'ALTER TABLE payments DROP CONSTRAINT ' || r.constraint_name;
        END LOOP;
    END;

    -- Rename chat_id → user_id in payments if the old column still exists
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'payments' AND column_name = 'chat_id'
    ) AND NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'payments' AND column_name = 'user_id'
    ) THEN
        ALTER TABLE payments RENAME COLUMN chat_id TO user_id;
    END IF;

    -- Rename chat_id → user_id in activity_log if the old column still exists
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'activity_log' AND column_name = 'chat_id'
    ) AND NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'activity_log' AND column_name = 'user_id'
    ) THEN
        ALTER TABLE activity_log RENAME COLUMN chat_id TO user_id;
    END IF;
END$$;
"""


@contextmanager
def get_db():
    conn = psycopg2.connect(_db_url)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(db_url: str = None):
    """Initialise the database schema.

    Args:
        db_url: Override the database URL. Pass cfg.db_url when using the plugin.
                Falls back to the DATABASE_URL environment variable.
    """
    global _db_url
    if db_url:
        _db_url = db_url
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(_MIGRATIONS)
            cur.execute(SCHEMA)
    log.info("Database initialized")


def log_activity(user_id: int, action: str, detail: str = ""):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO activity_log (user_id, action, detail) VALUES (%s, %s, %s)",
                (user_id, action, detail),
            )


# ── Users ──────────────────────────────────────────────

def upsert_user(chat_id: int, username: str = None, first_name: str = None):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO users (chat_id, username, first_name)
                VALUES (%s, %s, %s)
                ON CONFLICT (chat_id) DO UPDATE
                SET username = COALESCE(EXCLUDED.username, users.username),
                    first_name = COALESCE(EXCLUDED.first_name, users.first_name),
                    last_seen = NOW()
            """, (chat_id, username, first_name))


def is_blocked(chat_id: int) -> bool:
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT is_blocked FROM users WHERE chat_id = %s", (chat_id,))
            row = cur.fetchone()
            return row[0] if row else False


def get_user(chat_id: int) -> dict | None:
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM users WHERE chat_id = %s", (chat_id,))
            return cur.fetchone()


def block_user(chat_id: int) -> bool:
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE users SET is_blocked = TRUE WHERE chat_id = %s", (chat_id,))
            return cur.rowcount > 0


def unblock_user(chat_id: int) -> bool:
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE users SET is_blocked = FALSE WHERE chat_id = %s", (chat_id,))
            return cur.rowcount > 0


# ── Payments ───────────────────────────────────────────

def is_amount_in_use(amount: float) -> bool:
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT 1 FROM payments
                WHERE ABS(session_amount - %s) < 0.001 AND status = 'PENDING' AND expire_at > NOW()
                LIMIT 1
            """, (amount,))
            return cur.fetchone() is not None


def insert_payment(order_id: str, user_id: int, base_amount: float,
                   session_amount: float, expire_at: datetime, message_id: int = None):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO payments (order_id, user_id, base_amount, session_amount, expire_at, message_id)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (order_id, user_id, base_amount, session_amount, expire_at, message_id))


def complete_payment(order_id: str, utr: str, vpa: str = ""):
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                UPDATE payments SET status='SUCCESS', utr=%s, payer_vpa=%s, completed_at=NOW()
                WHERE order_id=%s AND status='PENDING'
                RETURNING user_id, session_amount
            """, (utr, vpa, order_id))
            row = cur.fetchone()
            if row:
                cur.execute("""
                    UPDATE users SET total_paid = total_paid + %s, payment_count = payment_count + 1
                    WHERE chat_id = %s
                """, (row["session_amount"], row["user_id"]))


def fail_payment(order_id: str):
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                UPDATE payments SET status='FAILURE'
                WHERE order_id=%s AND status='PENDING'
                RETURNING user_id
            """, (order_id,))
            row = cur.fetchone()
            if row:
                cur.execute(
                    "UPDATE users SET failed_count = failed_count + 1 WHERE chat_id = %s",
                    (row["user_id"],),
                )


def get_payment(order_id: str) -> dict | None:
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM payments WHERE order_id = %s", (order_id,))
            return cur.fetchone()


def expire_stale():
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE payments SET status='FAILURE' WHERE status='PENDING' AND expire_at < NOW()")
            return cur.rowcount


def user_history(user_id: int, limit: int = 10) -> list[dict]:
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT order_id, session_amount, status, utr, payer_vpa, created_at, completed_at
                FROM payments WHERE user_id = %s ORDER BY created_at DESC LIMIT %s
            """, (user_id, limit))
            return cur.fetchall()


def user_active_count(user_id: int) -> int:
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM payments WHERE user_id=%s AND status='PENDING' AND expire_at > NOW()",
                (user_id,),
            )
            return cur.fetchone()[0]


def user_hourly_count(user_id: int) -> int:
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM payments WHERE user_id=%s AND created_at > NOW() - INTERVAL '1 hour'",
                (user_id,),
            )
            return cur.fetchone()[0]


# ── Admin ──────────────────────────────────────────────

def admin_dashboard() -> dict:
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT
                    COUNT(*) AS total,
                    COUNT(*) FILTER (WHERE status='SUCCESS') AS success,
                    COUNT(*) FILTER (WHERE status='FAILURE') AS failed,
                    COUNT(*) FILTER (WHERE status='PENDING') AS pending,
                    COALESCE(SUM(session_amount) FILTER (WHERE status='SUCCESS'), 0) AS revenue,
                    COALESCE(SUM(session_amount) FILTER (WHERE status='SUCCESS' AND created_at::date = CURRENT_DATE), 0) AS today_rev,
                    COUNT(*) FILTER (WHERE status='SUCCESS' AND created_at::date = CURRENT_DATE) AS today_count
                FROM payments
            """)
            d = cur.fetchone()
            cur.execute("SELECT COUNT(*) AS c FROM users")
            d["users"] = cur.fetchone()["c"]
            cur.execute("SELECT COUNT(*) AS c FROM users WHERE last_seen > NOW() - INTERVAL '24 hours'")
            d["active"] = cur.fetchone()["c"]
            cur.execute("SELECT COUNT(*) AS c FROM users WHERE is_blocked")
            d["blocked"] = cur.fetchone()["c"]
            return d


def admin_recent(limit: int = 10) -> list[dict]:
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT p.*, u.username, u.first_name
                FROM payments p LEFT JOIN users u ON p.user_id = u.chat_id
                ORDER BY p.created_at DESC LIMIT %s
            """, (limit,))
            return cur.fetchall()


def admin_users(limit: int = 20) -> list[dict]:
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM users ORDER BY total_paid DESC LIMIT %s", (limit,))
            return cur.fetchall()


def admin_search(q: str) -> dict | None:
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM payments WHERE order_id=%s OR utr=%s LIMIT 1", (q, q))
            return cur.fetchone()
