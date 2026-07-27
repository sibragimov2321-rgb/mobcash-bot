from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class UserProfile:
    telegram_id: int
    username: str | None
    full_name: str
    language: str | None
    accepted_terms_at: str | None
    referred_by: int | None
    referral_balance_minor: int


@dataclass(frozen=True, slots=True)
class Operation:
    id: int
    user_id: int
    kind: str
    amount_minor: int
    payment_total_minor: int | None
    card_id: int | None
    platform: str | None
    platform_account_id: str | None
    payment_method: str | None
    payout_details: str | None
    payout_file_id: str | None
    payout_file_type: str | None
    request_code: str | None
    proof_file_id: str | None
    proof_type: str | None
    source: str
    status: str
    created_at: str
    processed_by: int | None


@dataclass(frozen=True, slots=True)
class Statistics:
    deposits_today_minor: int
    deposits_month_minor: int
    withdrawals_today_minor: int
    withdrawals_month_minor: int


@dataclass(frozen=True, slots=True)
class Card:
    id: int
    bank_key: str
    bank_name: str
    card_number: str
    holder_name: str
    direction: str
    status: str
    single_limit_minor: int
    daily_limit_minor: int
    daily_used_minor: int
    daily_reserved_minor: int
    usage_date: str
    priority: int
    created_by: int
    created_at: str


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 15000")
        return connection

    @staticmethod
    def _ensure_column(
        connection: sqlite3.Connection,
        table: str,
        column: str,
        definition: str,
    ) -> None:
        columns = {
            str(row["name"])
            for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if column not in columns:
            connection.execute(
                f"ALTER TABLE {table} ADD COLUMN {column} {definition}"
            )

    def initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                PRAGMA journal_mode = WAL;

                CREATE TABLE IF NOT EXISTS users (
                    telegram_id INTEGER PRIMARY KEY,
                    username TEXT,
                    full_name TEXT NOT NULL,
                    balance_minor INTEGER NOT NULL DEFAULT 0
                        CHECK (balance_minor >= 0),
                    language TEXT,
                    accepted_terms_at TEXT,
                    referred_by INTEGER,
                    referral_balance_minor INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS operations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL REFERENCES users(telegram_id),
                    kind TEXT NOT NULL CHECK (kind IN ('deposit', 'withdrawal')),
                    amount_minor INTEGER NOT NULL CHECK (amount_minor > 0),
                    payment_total_minor INTEGER,
                    details TEXT,
                    proof_file_id TEXT,
                    proof_type TEXT,
                    platform TEXT,
                    platform_account_id TEXT,
                    payment_method TEXT,
                    payout_details TEXT,
                    payout_file_id TEXT,
                    payout_file_type TEXT,
                    request_code TEXT,
                    source TEXT NOT NULL DEFAULT 'service',
                    status TEXT NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending', 'approved', 'rejected')),
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    processed_at TEXT,
                    processed_by INTEGER
                );

                CREATE TABLE IF NOT EXISTS platform_accounts (
                    user_id INTEGER NOT NULL REFERENCES users(telegram_id),
                    platform TEXT NOT NULL,
                    account_id TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (user_id, platform)
                );

                CREATE TABLE IF NOT EXISTS payment_reservations (
                    user_id INTEGER PRIMARY KEY REFERENCES users(telegram_id),
                    suffix_minor INTEGER NOT NULL UNIQUE
                        CHECK (suffix_minor BETWEEN 1 AND 99),
                    requested_amount_minor INTEGER NOT NULL,
                    payment_total_minor INTEGER NOT NULL UNIQUE,
                    payment_method_key TEXT,
                    card_id INTEGER,
                    expires_at INTEGER NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS staff (
                    user_id INTEGER PRIMARY KEY,
                    role TEXT NOT NULL CHECK (
                        role IN ('owner', 'supervisor', 'manager')
                    ),
                    username TEXT,
                    display_name TEXT NOT NULL DEFAULT '',
                    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
                    added_by INTEGER,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS system_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_by INTEGER,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS platform_settings (
                    key TEXT PRIMARY KEY,
                    display_name TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
                    updated_by INTEGER,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS payment_method_settings (
                    key TEXT PRIMARY KEY,
                    display_name TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
                    updated_by INTEGER,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS cards (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    bank_key TEXT NOT NULL,
                    bank_name TEXT NOT NULL,
                    card_number TEXT NOT NULL,
                    holder_name TEXT NOT NULL DEFAULT '',
                    direction TEXT NOT NULL DEFAULT 'deposit' CHECK (
                        direction IN ('deposit', 'withdrawal', 'both')
                    ),
                    status TEXT NOT NULL DEFAULT 'reserve' CHECK (
                        status IN ('active', 'reserve', 'disabled', 'limit_reached')
                    ),
                    single_limit_minor INTEGER NOT NULL DEFAULT 0 CHECK (
                        single_limit_minor >= 0
                    ),
                    daily_limit_minor INTEGER NOT NULL DEFAULT 0 CHECK (
                        daily_limit_minor >= 0
                    ),
                    daily_used_minor INTEGER NOT NULL DEFAULT 0 CHECK (
                        daily_used_minor >= 0
                    ),
                    daily_reserved_minor INTEGER NOT NULL DEFAULT 0 CHECK (
                        daily_reserved_minor >= 0
                    ),
                    usage_date TEXT NOT NULL DEFAULT (date('now', 'localtime')),
                    priority INTEGER NOT NULL DEFAULT 100,
                    created_by INTEGER NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS audit_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    actor_id INTEGER NOT NULL,
                    actor_role TEXT NOT NULL,
                    action TEXT NOT NULL,
                    entity_type TEXT,
                    entity_id TEXT,
                    details TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                """
            )

            for column, definition in (
                ("language", "TEXT"),
                ("accepted_terms_at", "TEXT"),
                ("referred_by", "INTEGER"),
                ("referral_balance_minor", "INTEGER NOT NULL DEFAULT 0"),
            ):
                self._ensure_column(connection, "users", column, definition)

            for column, definition in (
                ("payment_total_minor", "INTEGER"),
                ("card_id", "INTEGER"),
                ("platform", "TEXT"),
                ("platform_account_id", "TEXT"),
                ("payment_method", "TEXT"),
                ("payout_details", "TEXT"),
                ("payout_file_id", "TEXT"),
                ("payout_file_type", "TEXT"),
                ("request_code", "TEXT"),
                ("source", "TEXT NOT NULL DEFAULT 'service'"),
            ):
                self._ensure_column(connection, "operations", column, definition)

            for column, definition in (
                ("payment_method_key", "TEXT"),
                ("card_id", "INTEGER"),
            ):
                self._ensure_column(
                    connection, "payment_reservations", column, definition
                )

            connection.executescript(
                """
                CREATE INDEX IF NOT EXISTS idx_operations_user
                ON operations(user_id, id DESC);

                CREATE INDEX IF NOT EXISTS idx_operations_status
                ON operations(status, id);

                CREATE INDEX IF NOT EXISTS idx_operations_created
                ON operations(created_at);

                CREATE INDEX IF NOT EXISTS idx_payment_reservations_expires
                ON payment_reservations(expires_at);

                CREATE INDEX IF NOT EXISTS idx_cards_bank_status
                ON cards(bank_key, status, priority, id);

                CREATE INDEX IF NOT EXISTS idx_audit_logs_created
                ON audit_logs(created_at DESC);
                """
            )

    def upsert_user(
        self, telegram_id: int, username: str | None, full_name: str
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO users (telegram_id, username, full_name)
                VALUES (?, ?, ?)
                ON CONFLICT(telegram_id) DO UPDATE SET
                    username = excluded.username,
                    full_name = excluded.full_name,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (telegram_id, username, full_name),
            )

    def get_user(self, telegram_id: int) -> UserProfile | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM users WHERE telegram_id = ?",
                (telegram_id,),
            ).fetchone()
        return self._user_from_row(row) if row else None

    def set_language(self, telegram_id: int, language: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE users
                SET language = ?, updated_at = CURRENT_TIMESTAMP
                WHERE telegram_id = ?
                """,
                (language, telegram_id),
            )

    def accept_terms(self, telegram_id: int) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE users
                SET accepted_terms_at = COALESCE(
                        accepted_terms_at,
                        datetime('now', 'localtime')
                    ),
                    updated_at = CURRENT_TIMESTAMP
                WHERE telegram_id = ?
                """,
                (telegram_id,),
            )

    def set_referrer(self, user_id: int, referrer_id: int) -> bool:
        if user_id == referrer_id:
            return False
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            referrer = connection.execute(
                "SELECT 1 FROM users WHERE telegram_id = ?",
                (referrer_id,),
            ).fetchone()
            user = connection.execute(
                "SELECT referred_by FROM users WHERE telegram_id = ?",
                (user_id,),
            ).fetchone()
            if referrer is None or user is None or user["referred_by"] is not None:
                connection.rollback()
                return False
            connection.execute(
                "UPDATE users SET referred_by = ? WHERE telegram_id = ?",
                (referrer_id, user_id),
            )
            connection.commit()
            return True
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def save_platform_account(
        self, user_id: int, platform: str, account_id: str
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO platform_accounts (user_id, platform, account_id)
                VALUES (?, ?, ?)
                ON CONFLICT(user_id, platform) DO UPDATE SET
                    account_id = excluded.account_id,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (user_id, platform, account_id),
            )

    def get_platform_account(self, user_id: int, platform: str) -> str | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT account_id FROM platform_accounts
                WHERE user_id = ? AND platform = ?
                """,
                (user_id, platform),
            ).fetchone()
        return str(row["account_id"]) if row else None

    def list_platform_accounts(self, user_id: int) -> list[tuple[str, str]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT platform, account_id FROM platform_accounts
                WHERE user_id = ?
                ORDER BY platform
                """,
                (user_id,),
            ).fetchall()
        return [(str(row["platform"]), str(row["account_id"])) for row in rows]

    def reserve_payment_amount(
        self,
        user_id: int,
        requested_amount_minor: int,
        whole_amount_minor: int,
        ttl_seconds: int,
    ) -> int:
        if whole_amount_minor <= 0 or whole_amount_minor % 100 != 0:
            raise ValueError("whole_amount_minor must contain whole currency units")
        now = int(time.time())
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            expired = connection.execute(
                """
                SELECT card_id, payment_total_minor
                FROM payment_reservations
                WHERE expires_at <= ? AND card_id IS NOT NULL
                """,
                (now,),
            ).fetchall()
            for row in expired:
                connection.execute(
                    """
                    UPDATE cards
                    SET daily_reserved_minor = MAX(
                            0, daily_reserved_minor - ?
                        ),
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (int(row["payment_total_minor"]), int(row["card_id"])),
                )
            connection.execute(
                "DELETE FROM payment_reservations WHERE expires_at <= ?",
                (now,),
            )
            existing = connection.execute(
                """
                SELECT card_id, payment_total_minor
                FROM payment_reservations WHERE user_id = ?
                """,
                (user_id,),
            ).fetchone()
            if existing is not None and existing["card_id"] is not None:
                connection.execute(
                    """
                    UPDATE cards
                    SET daily_reserved_minor = MAX(
                            0, daily_reserved_minor - ?
                        ),
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (
                        int(existing["payment_total_minor"]),
                        int(existing["card_id"]),
                    ),
                )
            connection.execute(
                "DELETE FROM payment_reservations WHERE user_id = ?", (user_id,)
            )
            used = {
                int(row["suffix_minor"])
                for row in connection.execute(
                    "SELECT suffix_minor FROM payment_reservations"
                ).fetchall()
            }
            preferred = abs(user_id) % 99 + 1
            suffix_minor = next(
                (
                    (preferred - 1 + offset) % 99 + 1
                    for offset in range(99)
                    if (preferred - 1 + offset) % 99 + 1 not in used
                ),
                None,
            )
            if suffix_minor is None:
                raise RuntimeError("All payment suffixes are currently reserved")
            payment_total_minor = whole_amount_minor + suffix_minor
            connection.execute(
                """
                INSERT INTO payment_reservations (
                    user_id, suffix_minor, requested_amount_minor,
                    payment_total_minor, expires_at
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    suffix_minor,
                    requested_amount_minor,
                    payment_total_minor,
                    now + ttl_seconds,
                ),
            )
            connection.commit()
            return payment_total_minor
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def release_payment_reservation(self, user_id: int) -> None:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT card_id, payment_total_minor
                FROM payment_reservations WHERE user_id = ?
                """,
                (user_id,),
            ).fetchone()
            if row is not None and row["card_id"] is not None:
                connection.execute(
                    """
                    UPDATE cards
                    SET daily_reserved_minor = MAX(
                            0, daily_reserved_minor - ?
                        ),
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (int(row["payment_total_minor"]), int(row["card_id"])),
                )
            connection.execute(
                "DELETE FROM payment_reservations WHERE user_id = ?",
                (user_id,),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def create_deposit(
        self,
        user_id: int,
        amount_minor: int,
        payment_total_minor: int,
        card_id: int | None,
        platform: str,
        platform_account_id: str,
        payment_method: str,
        proof_file_id: str,
        proof_type: str,
    ) -> Operation:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO operations (
                    user_id, kind, amount_minor, payment_total_minor, card_id, platform,
                    platform_account_id, payment_method,
                    proof_file_id, proof_type, source
                )
                VALUES (?, 'deposit', ?, ?, ?, ?, ?, ?, ?, ?, 'service')
                """,
                (
                    user_id,
                    amount_minor,
                    payment_total_minor,
                    card_id,
                    platform,
                    platform_account_id,
                    payment_method,
                    proof_file_id,
                    proof_type,
                ),
            )
            operation_id = int(cursor.lastrowid)
            row = connection.execute(
                "SELECT * FROM operations WHERE id = ?", (operation_id,)
            ).fetchone()
        return self._operation_from_row(row)

    def create_withdrawal(
        self,
        user_id: int,
        amount_minor: int,
        platform: str,
        platform_account_id: str,
        payout_details: str | None,
        payout_file_id: str | None,
        payout_file_type: str | None,
        request_code: str | None,
        proof_file_id: str | None,
        proof_type: str | None,
    ) -> Operation:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO operations (
                    user_id, kind, amount_minor, platform,
                    platform_account_id, payout_details,
                    payout_file_id, payout_file_type, request_code,
                    proof_file_id, proof_type, source
                )
                VALUES (?, 'withdrawal', ?, ?, ?, ?, ?, ?, ?, ?, ?, 'service')
                """,
                (
                    user_id,
                    amount_minor,
                    platform,
                    platform_account_id,
                    payout_details,
                    payout_file_id,
                    payout_file_type,
                    request_code,
                    proof_file_id,
                    proof_type,
                ),
            )
            operation_id = int(cursor.lastrowid)
            row = connection.execute(
                "SELECT * FROM operations WHERE id = ?", (operation_id,)
            ).fetchone()
        return self._operation_from_row(row)

    def create_referral_withdrawal(
        self,
        user_id: int,
        platform: str,
        platform_account_id: str,
        minimum_minor: int,
    ) -> Operation | None:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT referral_balance_minor FROM users
                WHERE telegram_id = ?
                """,
                (user_id,),
            ).fetchone()
            if row is None or int(row["referral_balance_minor"]) < minimum_minor:
                connection.rollback()
                return None
            amount_minor = int(row["referral_balance_minor"])
            connection.execute(
                """
                UPDATE users
                SET referral_balance_minor = 0,
                    updated_at = CURRENT_TIMESTAMP
                WHERE telegram_id = ?
                """,
                (user_id,),
            )
            cursor = connection.execute(
                """
                INSERT INTO operations (
                    user_id, kind, amount_minor, platform,
                    platform_account_id, source
                )
                VALUES (?, 'withdrawal', ?, ?, ?, 'referral')
                """,
                (user_id, amount_minor, platform, platform_account_id),
            )
            operation_id = int(cursor.lastrowid)
            row = connection.execute(
                "SELECT * FROM operations WHERE id = ?", (operation_id,)
            ).fetchone()
            connection.commit()
            return self._operation_from_row(row)
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def process_operation(
        self,
        operation_id: int,
        decision: str,
        admin_id: int,
        referral_percent_basis_points: int,
    ) -> tuple[bool, str, Operation | None, int | None, int]:
        if decision not in {"approved", "rejected"}:
            raise ValueError("Unknown decision")

        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM operations WHERE id = ?", (operation_id,)
            ).fetchone()
            if row is None:
                connection.rollback()
                return False, "Заявка не найдена.", None, None, 0

            operation = self._operation_from_row(row)
            if operation.status != "pending":
                connection.rollback()
                return False, "Эта заявка уже обработана.", operation, None, 0

            referrer_id: int | None = None
            referral_bonus = 0
            if (
                operation.kind == "deposit"
                and decision == "approved"
                and referral_percent_basis_points > 0
            ):
                user_row = connection.execute(
                    "SELECT referred_by FROM users WHERE telegram_id = ?",
                    (operation.user_id,),
                ).fetchone()
                if user_row and user_row["referred_by"] is not None:
                    referrer_id = int(user_row["referred_by"])
                    referral_bonus = (
                        operation.amount_minor * referral_percent_basis_points
                    ) // 10000
                    if referral_bonus > 0:
                        connection.execute(
                            """
                            UPDATE users
                            SET referral_balance_minor =
                                    referral_balance_minor + ?,
                                updated_at = CURRENT_TIMESTAMP
                            WHERE telegram_id = ?
                            """,
                            (referral_bonus, referrer_id),
                        )

            if (
                operation.kind == "withdrawal"
                and operation.source == "referral"
                and decision == "rejected"
            ):
                connection.execute(
                    """
                    UPDATE users
                    SET referral_balance_minor =
                            referral_balance_minor + ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE telegram_id = ?
                    """,
                    (operation.amount_minor, operation.user_id),
                )

            connection.execute(
                """
                UPDATE operations
                SET status = ?,
                    processed_at = datetime('now', 'localtime'),
                    processed_by = ?
                WHERE id = ?
                """,
                (decision, admin_id, operation_id),
            )
            updated_row = connection.execute(
                "SELECT * FROM operations WHERE id = ?", (operation_id,)
            ).fetchone()
            connection.commit()
            return (
                True,
                "Заявка обработана.",
                self._operation_from_row(updated_row),
                referrer_id,
                referral_bonus,
            )
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def sync_admin_configuration(
        self,
        owner_ids: frozenset[int],
        platforms: tuple[str, ...],
        payment_methods: tuple[tuple[str, str], ...],
    ) -> None:
        with self._connect() as connection:
            for owner_id in owner_ids:
                profile = connection.execute(
                    "SELECT username, full_name FROM users WHERE telegram_id = ?",
                    (owner_id,),
                ).fetchone()
                connection.execute(
                    """
                    INSERT INTO staff (
                        user_id, role, username, display_name, active, added_by
                    ) VALUES (?, 'owner', ?, ?, 1, ?)
                    ON CONFLICT(user_id) DO UPDATE SET
                        role = 'owner', active = 1,
                        username = COALESCE(excluded.username, staff.username),
                        display_name = CASE
                            WHEN excluded.display_name <> ''
                            THEN excluded.display_name ELSE staff.display_name END,
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    (
                        owner_id,
                        profile["username"] if profile else None,
                        str(profile["full_name"]) if profile else "Владелец",
                        owner_id,
                    ),
                )
            for platform in platforms:
                connection.execute(
                    """
                    INSERT INTO platform_settings (key, display_name, enabled)
                    VALUES (?, ?, 1)
                    ON CONFLICT(key) DO UPDATE SET
                        display_name = excluded.display_name
                    """,
                    (platform.upper(), platform),
                )
            for key, name in payment_methods:
                connection.execute(
                    """
                    INSERT INTO payment_method_settings (
                        key, display_name, enabled
                    ) VALUES (?, ?, 1)
                    ON CONFLICT(key) DO UPDATE SET
                        display_name = excluded.display_name
                    """,
                    (key.upper(), name),
                )
            defaults = {
                "maintenance_mode": "0",
                "log_chat_id": "",
                "support_contact": "",
                "offer_text": "",
                "profit_deposit_percent": "0",
                "profit_withdraw_percent": "0",
            }
            for key, value in defaults.items():
                connection.execute(
                    """
                    INSERT OR IGNORE INTO system_settings (key, value)
                    VALUES (?, ?)
                    """,
                    (key, value),
                )

    def get_staff_role(self, user_id: int) -> str | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT role FROM staff WHERE user_id = ? AND active = 1",
                (user_id,),
            ).fetchone()
        return str(row["role"]) if row else None

    def list_staff(self, role: str | None = None) -> list[dict[str, object]]:
        query = """
            SELECT s.user_id, s.role,
                   COALESCE(s.username, u.username) AS username,
                   CASE WHEN s.display_name <> '' THEN s.display_name
                        ELSE COALESCE(u.full_name, '') END AS display_name,
                   s.created_at
            FROM staff s
            LEFT JOIN users u ON u.telegram_id = s.user_id
            WHERE s.active = 1
        """
        params: tuple[object, ...] = ()
        if role is not None:
            query += " AND s.role = ?"
            params = (role,)
        query += " ORDER BY CASE s.role WHEN 'owner' THEN 1 WHEN 'supervisor' THEN 2 ELSE 3 END, s.user_id"
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def resolve_user(self, identifier: str) -> dict[str, object] | None:
        normalized = identifier.strip().lstrip("@").lower()
        with self._connect() as connection:
            if normalized.isdigit():
                row = connection.execute(
                    """
                    SELECT telegram_id AS user_id, username, full_name
                    FROM users WHERE telegram_id = ?
                    """,
                    (int(normalized),),
                ).fetchone()
            else:
                row = connection.execute(
                    """
                    SELECT telegram_id AS user_id, username, full_name
                    FROM users WHERE lower(username) = ?
                    """,
                    (normalized,),
                ).fetchone()
        return dict(row) if row else None

    def set_staff_role(
        self, user_id: int, role: str, added_by: int
    ) -> dict[str, object]:
        if role not in {"supervisor", "manager"}:
            raise ValueError("Unsupported staff role")
        with self._connect() as connection:
            profile = connection.execute(
                "SELECT username, full_name FROM users WHERE telegram_id = ?",
                (user_id,),
            ).fetchone()
            if profile is None:
                raise LookupError("User must start the bot first")
            connection.execute(
                """
                INSERT INTO staff (
                    user_id, role, username, display_name, active, added_by
                ) VALUES (?, ?, ?, ?, 1, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    role = excluded.role,
                    username = excluded.username,
                    display_name = excluded.display_name,
                    active = 1,
                    added_by = excluded.added_by,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    user_id,
                    role,
                    profile["username"],
                    str(profile["full_name"]),
                    added_by,
                ),
            )
        return {
            "user_id": user_id,
            "role": role,
            "username": profile["username"],
            "display_name": str(profile["full_name"]),
        }

    def remove_staff(self, user_id: int) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE staff SET active = 0, updated_at = CURRENT_TIMESTAMP
                WHERE user_id = ? AND role <> 'owner' AND active = 1
                """,
                (user_id,),
            )
        return cursor.rowcount > 0

    def get_setting(self, key: str, default: str = "") -> str:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT value FROM system_settings WHERE key = ?", (key,)
            ).fetchone()
        return str(row["value"]) if row else default

    def get_setting_bool(self, key: str, default: bool = False) -> bool:
        return self.get_setting(key, "1" if default else "0") in {
            "1",
            "true",
            "yes",
            "on",
        }

    def set_setting(self, key: str, value: str, updated_by: int) -> str:
        old_value = self.get_setting(key, "")
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO system_settings (key, value, updated_by)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_by = excluded.updated_by,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (key, value, updated_by),
            )
        return old_value

    def list_platform_settings(self) -> list[dict[str, object]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT key, display_name, enabled FROM platform_settings ORDER BY rowid"
            ).fetchall()
        return [dict(row) for row in rows]

    def is_platform_enabled(self, key: str) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT enabled FROM platform_settings WHERE key = ?",
                (key.upper(),),
            ).fetchone()
        return bool(row["enabled"]) if row else False

    def toggle_platform(self, key: str, updated_by: int) -> bool:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE platform_settings
                SET enabled = CASE enabled WHEN 1 THEN 0 ELSE 1 END,
                    updated_by = ?, updated_at = CURRENT_TIMESTAMP
                WHERE key = ?
                """,
                (updated_by, key.upper()),
            )
            row = connection.execute(
                "SELECT enabled FROM platform_settings WHERE key = ?",
                (key.upper(),),
            ).fetchone()
        if row is None:
            raise LookupError("Platform not found")
        return bool(row["enabled"])

    def list_payment_method_settings(self) -> list[dict[str, object]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT key, display_name, enabled
                FROM payment_method_settings ORDER BY rowid
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def is_payment_method_enabled(self, key: str) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT enabled FROM payment_method_settings WHERE key = ?",
                (key.upper(),),
            ).fetchone()
        return bool(row["enabled"]) if row else False

    def toggle_payment_method(self, key: str, updated_by: int) -> bool:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE payment_method_settings
                SET enabled = CASE enabled WHEN 1 THEN 0 ELSE 1 END,
                    updated_by = ?, updated_at = CURRENT_TIMESTAMP
                WHERE key = ?
                """,
                (updated_by, key.upper()),
            )
            row = connection.execute(
                "SELECT enabled FROM payment_method_settings WHERE key = ?",
                (key.upper(),),
            ).fetchone()
        if row is None:
            raise LookupError("Payment method not found")
        return bool(row["enabled"])

    def list_user_ids(self) -> list[int]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT telegram_id FROM users ORDER BY telegram_id"
            ).fetchall()
        return [int(row["telegram_id"]) for row in rows]

    def add_audit_log(
        self,
        actor_id: int,
        actor_role: str,
        action: str,
        entity_type: str | None = None,
        entity_id: str | None = None,
        details: str | None = None,
    ) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO audit_logs (
                    actor_id, actor_role, action,
                    entity_type, entity_id, details
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    actor_id,
                    actor_role,
                    action,
                    entity_type,
                    entity_id,
                    details,
                ),
            )
        return int(cursor.lastrowid)

    def get_admin_statistics(self) -> dict[str, dict[str, int]]:
        periods = {
            "today": "date('now', '+6 hours')",
            "week": "date('now', '+6 hours', '-6 days')",
            "month": "date('now', '+6 hours', 'start of month')",
        }
        result: dict[str, dict[str, int]] = {}
        with self._connect() as connection:
            for name, start_expression in periods.items():
                row = connection.execute(
                    f"""
                    SELECT
                        COALESCE(SUM(CASE WHEN kind = 'deposit'
                            THEN amount_minor ELSE 0 END), 0) AS deposits,
                        COALESCE(SUM(CASE WHEN kind = 'withdrawal'
                            AND source = 'service'
                            THEN amount_minor ELSE 0 END), 0) AS withdrawals,
                        SUM(CASE WHEN kind = 'deposit' THEN 1 ELSE 0 END)
                            AS deposit_count,
                        SUM(CASE WHEN kind = 'withdrawal'
                            AND source = 'service' THEN 1 ELSE 0 END)
                            AS withdrawal_count
                    FROM operations
                    WHERE status = 'approved'
                      AND date(
                          COALESCE(processed_at, created_at), '+6 hours'
                      ) >= {start_expression}
                    """
                ).fetchone()
                result[name] = {
                    "deposits": int(row["deposits"] or 0),
                    "withdrawals": int(row["withdrawals"] or 0),
                    "deposit_count": int(row["deposit_count"] or 0),
                    "withdrawal_count": int(row["withdrawal_count"] or 0),
                }
            pending = connection.execute(
                "SELECT COUNT(*) AS count FROM operations WHERE status = 'pending'"
            ).fetchone()
            users = connection.execute(
                "SELECT COUNT(*) AS count FROM users"
            ).fetchone()
        result["technical"] = {
            "pending": int(pending["count"]),
            "users": int(users["count"]),
        }
        return result

    @staticmethod
    def _reset_card_day(connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            UPDATE cards
            SET daily_used_minor = 0,
                daily_reserved_minor = 0,
                usage_date = date('now', '+6 hours'),
                status = CASE WHEN status = 'limit_reached'
                    THEN 'reserve' ELSE status END,
                updated_at = CURRENT_TIMESTAMP
            WHERE usage_date <> date('now', '+6 hours')
            """
        )

    def add_card(
        self,
        bank_key: str,
        bank_name: str,
        card_number: str,
        holder_name: str,
        direction: str,
        status: str,
        single_limit_minor: int,
        daily_limit_minor: int,
        created_by: int,
    ) -> Card:
        if direction not in {"deposit", "withdrawal", "both"}:
            raise ValueError("Invalid card direction")
        if status not in {"active", "reserve", "disabled"}:
            raise ValueError("Invalid initial card status")
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO cards (
                    bank_key, bank_name, card_number, holder_name,
                    direction, status, single_limit_minor,
                    daily_limit_minor, usage_date, created_by
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, date('now', '+6 hours'), ?)
                """,
                (
                    bank_key.upper(),
                    bank_name,
                    card_number,
                    holder_name,
                    direction,
                    status,
                    single_limit_minor,
                    daily_limit_minor,
                    created_by,
                ),
            )
            row = connection.execute(
                "SELECT * FROM cards WHERE id = ?", (int(cursor.lastrowid),)
            ).fetchone()
        return self._card_from_row(row)

    def list_cards(self, bank_key: str | None = None) -> list[Card]:
        with self._connect() as connection:
            self._reset_card_day(connection)
            if bank_key:
                rows = connection.execute(
                    """
                    SELECT * FROM cards WHERE bank_key = ?
                    ORDER BY priority, id
                    """,
                    (bank_key.upper(),),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM cards ORDER BY bank_key, priority, id"
                ).fetchall()
        return [self._card_from_row(row) for row in rows]

    def get_card(self, card_id: int) -> Card | None:
        with self._connect() as connection:
            self._reset_card_day(connection)
            row = connection.execute(
                "SELECT * FROM cards WHERE id = ?", (card_id,)
            ).fetchone()
        return self._card_from_row(row) if row else None

    def update_card_limit(
        self, card_id: int, limit_type: str, amount_minor: int
    ) -> tuple[int, Card]:
        column = {
            "daily": "daily_limit_minor",
            "single": "single_limit_minor",
        }.get(limit_type)
        if column is None or amount_minor < 0:
            raise ValueError("Invalid card limit")
        with self._connect() as connection:
            row = connection.execute(
                f"SELECT {column} AS old_value FROM cards WHERE id = ?",
                (card_id,),
            ).fetchone()
            if row is None:
                raise LookupError("Card not found")
            old_value = int(row["old_value"])
            connection.execute(
                f"""
                UPDATE cards SET {column} = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (amount_minor, card_id),
            )
            if limit_type == "daily":
                current = connection.execute(
                    """
                    SELECT bank_key, status, daily_used_minor,
                           daily_reserved_minor
                    FROM cards WHERE id = ?
                    """,
                    (card_id,),
                ).fetchone()
                consumed = int(current["daily_used_minor"]) + int(
                    current["daily_reserved_minor"]
                )
                if amount_minor > 0 and consumed >= amount_minor:
                    connection.execute(
                        "UPDATE cards SET status = 'limit_reached' WHERE id = ?",
                        (card_id,),
                    )
                    replacement = connection.execute(
                        """
                        SELECT id FROM cards
                        WHERE bank_key = ? AND status = 'reserve'
                          AND direction IN ('deposit', 'both')
                        ORDER BY priority, id LIMIT 1
                        """,
                        (str(current["bank_key"]),),
                    ).fetchone()
                    if replacement is not None:
                        connection.execute(
                            "UPDATE cards SET status = 'active' WHERE id = ?",
                            (int(replacement["id"]),),
                        )
                elif str(current["status"]) == "limit_reached":
                    connection.execute(
                        "UPDATE cards SET status = 'reserve' WHERE id = ?",
                        (card_id,),
                    )
            updated = connection.execute(
                "SELECT * FROM cards WHERE id = ?", (card_id,)
            ).fetchone()
        return old_value, self._card_from_row(updated)

    def set_card_status(self, card_id: int, status: str) -> tuple[str, Card]:
        if status not in {"active", "reserve", "disabled"}:
            raise ValueError("Invalid card status")
        with self._connect() as connection:
            row = connection.execute(
                "SELECT status FROM cards WHERE id = ?", (card_id,)
            ).fetchone()
            if row is None:
                raise LookupError("Card not found")
            old_status = str(row["status"])
            connection.execute(
                """
                UPDATE cards SET status = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (status, card_id),
            )
            updated = connection.execute(
                "SELECT * FROM cards WHERE id = ?", (card_id,)
            ).fetchone()
        return old_status, self._card_from_row(updated)

    def delete_card(self, card_id: int) -> Card | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM cards WHERE id = ?", (card_id,)
            ).fetchone()
            if row is None:
                return None
            connection.execute("DELETE FROM cards WHERE id = ?", (card_id,))
        return self._card_from_row(row)

    def card_status_counts(self) -> dict[str, int]:
        with self._connect() as connection:
            self._reset_card_day(connection)
            rows = connection.execute(
                "SELECT status, COUNT(*) AS count FROM cards GROUP BY status"
            ).fetchall()
        result = {"active": 0, "reserve": 0, "disabled": 0, "limit_reached": 0}
        result.update({str(row["status"]): int(row["count"]) for row in rows})
        return result

    def reserve_card_for_payment(
        self, user_id: int, bank_key: str
    ) -> Card | None:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._reset_card_day(connection)
            reservation = connection.execute(
                """
                SELECT card_id, payment_total_minor, payment_method_key
                FROM payment_reservations
                WHERE user_id = ? AND expires_at > strftime('%s', 'now')
                """,
                (user_id,),
            ).fetchone()
            if reservation is None:
                connection.rollback()
                return None
            amount_minor = int(reservation["payment_total_minor"])
            current_card_id = (
                int(reservation["card_id"])
                if reservation["card_id"] is not None
                else None
            )
            if (
                current_card_id is not None
                and str(reservation["payment_method_key"] or "").upper()
                == bank_key.upper()
            ):
                row = connection.execute(
                    "SELECT * FROM cards WHERE id = ?", (current_card_id,)
                ).fetchone()
                if row is not None:
                    connection.commit()
                    return self._card_from_row(row)
            if current_card_id is not None:
                connection.execute(
                    """
                    UPDATE cards
                    SET daily_reserved_minor = MAX(
                            0, daily_reserved_minor - ?
                        ), updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (amount_minor, current_card_id),
                )
            connection.execute(
                """
                UPDATE cards SET status = 'limit_reached',
                    updated_at = CURRENT_TIMESTAMP
                WHERE bank_key = ? AND status = 'active'
                  AND daily_limit_minor > 0
                  AND daily_used_minor + daily_reserved_minor
                      >= daily_limit_minor
                """,
                (bank_key.upper(),),
            )
            eligibility = """
                bank_key = ?
                AND direction IN ('deposit', 'both')
                AND (single_limit_minor = 0 OR single_limit_minor >= ?)
                AND (daily_limit_minor = 0 OR
                     daily_used_minor + daily_reserved_minor + ?
                     <= daily_limit_minor)
            """
            row = connection.execute(
                f"""
                SELECT * FROM cards
                WHERE {eligibility} AND status = 'active'
                ORDER BY priority, id LIMIT 1
                """,
                (bank_key.upper(), amount_minor, amount_minor),
            ).fetchone()
            if row is None:
                row = connection.execute(
                    f"""
                    SELECT * FROM cards
                    WHERE {eligibility} AND status = 'reserve'
                    ORDER BY priority, id LIMIT 1
                    """,
                    (bank_key.upper(), amount_minor, amount_minor),
                ).fetchone()
                if row is not None:
                    connection.execute(
                        "UPDATE cards SET status = 'active' WHERE id = ?",
                        (int(row["id"]),),
                    )
            if row is None:
                connection.execute(
                    """
                    UPDATE payment_reservations
                    SET card_id = NULL, payment_method_key = ?
                    WHERE user_id = ?
                    """,
                    (bank_key.upper(), user_id),
                )
                connection.commit()
                return None
            card_id = int(row["id"])
            connection.execute(
                """
                UPDATE cards
                SET daily_reserved_minor = daily_reserved_minor + ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (amount_minor, card_id),
            )
            connection.execute(
                """
                UPDATE payment_reservations
                SET card_id = ?, payment_method_key = ?
                WHERE user_id = ?
                """,
                (card_id, bank_key.upper(), user_id),
            )
            updated = connection.execute(
                "SELECT * FROM cards WHERE id = ?", (card_id,)
            ).fetchone()
            connection.commit()
            return self._card_from_row(updated)
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def finalize_payment_reservation(self, user_id: int) -> int | None:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._reset_card_day(connection)
            reservation = connection.execute(
                """
                SELECT card_id, payment_total_minor, payment_method_key
                FROM payment_reservations WHERE user_id = ?
                """,
                (user_id,),
            ).fetchone()
            if reservation is None:
                connection.commit()
                return None
            card_id = (
                int(reservation["card_id"])
                if reservation["card_id"] is not None
                else None
            )
            amount_minor = int(reservation["payment_total_minor"])
            bank_key = str(reservation["payment_method_key"] or "")
            if card_id is not None:
                connection.execute(
                    """
                    UPDATE cards SET
                        daily_reserved_minor = MAX(
                            0, daily_reserved_minor - ?
                        ),
                        daily_used_minor = daily_used_minor + ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (amount_minor, amount_minor, card_id),
                )
                connection.execute(
                    """
                    UPDATE cards SET status = 'limit_reached'
                    WHERE id = ? AND daily_limit_minor > 0
                      AND daily_used_minor + daily_reserved_minor
                          >= daily_limit_minor
                    """,
                    (card_id,),
                )
                status = connection.execute(
                    "SELECT status FROM cards WHERE id = ?", (card_id,)
                ).fetchone()
                if status is not None and status["status"] == "limit_reached":
                    replacement = connection.execute(
                        """
                        SELECT id FROM cards
                        WHERE bank_key = ? AND status = 'reserve'
                          AND direction IN ('deposit', 'both')
                        ORDER BY priority, id LIMIT 1
                        """,
                        (bank_key,),
                    ).fetchone()
                    if replacement is not None:
                        connection.execute(
                            "UPDATE cards SET status = 'active' WHERE id = ?",
                            (int(replacement["id"]),),
                        )
            connection.execute(
                "DELETE FROM payment_reservations WHERE user_id = ?", (user_id,)
            )
            connection.commit()
            return card_id
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def get_statistics(self, user_id: int) -> Statistics:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT
                    COALESCE(SUM(CASE
                        WHEN kind = 'deposit'
                         AND date(created_at) = date('now', 'localtime')
                        THEN amount_minor ELSE 0 END), 0) AS deposits_today,
                    COALESCE(SUM(CASE
                        WHEN kind = 'deposit'
                         AND strftime('%Y-%m', created_at) =
                             strftime('%Y-%m', 'now', 'localtime')
                        THEN amount_minor ELSE 0 END), 0) AS deposits_month,
                    COALESCE(SUM(CASE
                        WHEN kind = 'withdrawal'
                         AND source = 'service'
                         AND date(created_at) = date('now', 'localtime')
                        THEN amount_minor ELSE 0 END), 0) AS withdrawals_today,
                    COALESCE(SUM(CASE
                        WHEN kind = 'withdrawal'
                         AND source = 'service'
                         AND strftime('%Y-%m', created_at) =
                             strftime('%Y-%m', 'now', 'localtime')
                        THEN amount_minor ELSE 0 END), 0) AS withdrawals_month
                FROM operations
                WHERE user_id = ? AND status = 'approved'
                """,
                (user_id,),
            ).fetchone()
        return Statistics(
            deposits_today_minor=int(row["deposits_today"]),
            deposits_month_minor=int(row["deposits_month"]),
            withdrawals_today_minor=int(row["withdrawals_today"]),
            withdrawals_month_minor=int(row["withdrawals_month"]),
        )

    def list_user_operations(
        self, user_id: int, limit: int = 10
    ) -> list[Operation]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM operations
                WHERE user_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (user_id, limit),
            ).fetchall()
        return [self._operation_from_row(row) for row in rows]

    def list_pending_operations(self, limit: int = 20) -> list[Operation]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM operations
                WHERE status = 'pending'
                ORDER BY id
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [self._operation_from_row(row) for row in rows]

    @staticmethod
    def _user_from_row(row: sqlite3.Row) -> UserProfile:
        return UserProfile(
            telegram_id=int(row["telegram_id"]),
            username=row["username"],
            full_name=str(row["full_name"]),
            language=row["language"],
            accepted_terms_at=row["accepted_terms_at"],
            referred_by=(
                int(row["referred_by"]) if row["referred_by"] is not None else None
            ),
            referral_balance_minor=int(row["referral_balance_minor"] or 0),
        )

    @staticmethod
    def _operation_from_row(row: sqlite3.Row) -> Operation:
        return Operation(
            id=int(row["id"]),
            user_id=int(row["user_id"]),
            kind=str(row["kind"]),
            amount_minor=int(row["amount_minor"]),
            payment_total_minor=(
                int(row["payment_total_minor"])
                if row["payment_total_minor"] is not None
                else None
            ),
            card_id=(int(row["card_id"]) if row["card_id"] is not None else None),
            platform=row["platform"],
            platform_account_id=row["platform_account_id"],
            payment_method=row["payment_method"],
            payout_details=row["payout_details"] or row["details"],
            payout_file_id=row["payout_file_id"],
            payout_file_type=row["payout_file_type"],
            request_code=row["request_code"],
            proof_file_id=row["proof_file_id"],
            proof_type=row["proof_type"],
            source=str(row["source"] or "service"),
            status=str(row["status"]),
            created_at=str(row["created_at"]),
            processed_by=(
                int(row["processed_by"]) if row["processed_by"] is not None else None
            ),
        )

    @staticmethod
    def _card_from_row(row: sqlite3.Row) -> Card:
        return Card(
            id=int(row["id"]),
            bank_key=str(row["bank_key"]),
            bank_name=str(row["bank_name"]),
            card_number=str(row["card_number"]),
            holder_name=str(row["holder_name"]),
            direction=str(row["direction"]),
            status=str(row["status"]),
            single_limit_minor=int(row["single_limit_minor"]),
            daily_limit_minor=int(row["daily_limit_minor"]),
            daily_used_minor=int(row["daily_used_minor"]),
            daily_reserved_minor=int(row["daily_reserved_minor"]),
            usage_date=str(row["usage_date"]),
            priority=int(row["priority"]),
            created_by=int(row["created_by"]),
            created_at=str(row["created_at"]),
        )
