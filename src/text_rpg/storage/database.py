from __future__ import annotations

import contextlib
import importlib
import pathlib
import sqlite3
from typing import Generator


_MIGRATIONS = [
    "001_initial",
    "002_director",
    "003_gold_equipment",
    "004_survival_needs",
    "005_trade_skills",
    "006_spellcasting",
    "007_reputation",
    "008_world_clock",
    "009_shops",
    "010_death_wounds",
    "011_companions",
    "012_multiclass_housing",
    "013_connections",
    "014_snapshots",
    "015_class_resources",
    "016_traits",
    "017_elements_spells",
    "018_guilds_professions",
    "019_origin_id",
    "020_size",
    "021_custom_items",
    "022_local_turns",
    "023_save_scoped_content",
    "024_campaign_continuity",
    "025_continuity_events",
    "026_revival_offers",
    "027_branching_connections",
    "028_ambient_population",
    "029_ambient_roles",
    "030_ambient_crowds",
]


class Database:
    """Main database manager for the Text RPG storage layer."""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._connection: sqlite3.Connection | None = None
        self.active_game_id: str | None = None
        self._depth = 0
        self._failed = False
        self._staging = False
        pathlib.Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    def initialize(self) -> None:
        """Run migrations to create all tables, skipping already-applied ones."""
        conn = self._get_raw_connection()
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_version "
            "(version INTEGER PRIMARY KEY)"
        )
        applied = {
            r[0] for r in conn.execute("SELECT version FROM schema_version").fetchall()
        }
        for i, name in enumerate(_MIGRATIONS, 1):
            if i not in applied:
                mod = importlib.import_module(f"text_rpg.storage.migrations.{name}")
                mod.upgrade(conn)
                conn.execute("INSERT INTO schema_version VALUES (?)", (i,))
        conn.commit()

    def _get_raw_connection(self) -> sqlite3.Connection:
        """Return the shared connection, creating it if needed."""
        if self._connection is None:
            self._connection = sqlite3.connect(self.db_path)
            self._connection.row_factory = sqlite3.Row
            self._connection.execute("PRAGMA journal_mode=WAL")
            self._connection.execute("PRAGMA foreign_keys=ON")
        return self._connection

    @contextlib.contextmanager
    def get_connection(self) -> Generator[sqlite3.Connection, None, None]:
        """Context manager that yields a database connection.

        Commits on success, rolls back on exception.
        """
        conn = self._get_raw_connection()
        outer = self._depth == 0
        if outer:
            self._failed = False
        self._depth += 1
        try:
            yield conn
        except BaseException:
            self._failed = True
            raise
        finally:
            self._depth -= 1
            if outer:
                if self._failed:
                    conn.rollback()
                else:
                    conn.commit()
        if outer and self._failed:
            raise RuntimeError("A nested database operation failed; transaction aborted")

    transaction = get_connection

    @staticmethod
    def _rows(conn):
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ) if not r[0].startswith("utterance_search")]
        return {name: (tuple(r[1] for r in conn.execute(f'PRAGMA table_info("{name}")')),
                       {r[0]: tuple(r[1:]) for r in conn.execute(f'SELECT rowid, * FROM "{name}"')})
                for name in tables}

    @contextlib.contextmanager
    def staged_transaction(self):
        """Resolve legacy write-producing systems on an isolated SQLite copy.

        No persistent write lock is held during generation. Compare the source
        snapshot at commit, then apply only changed rows in one short transaction.
        Conflicts require a new intent resolution; never overwrite a newer save.
        This conservative whole-database conflict check also protects cross-table
        preconditions. It trades copy cost for compatibility with existing systems.
        """
        if self._staging or self._depth:
            raise RuntimeError("Staged turns cannot nest")
        source = self._get_raw_connection()
        shadow = sqlite3.connect(":memory:")
        shadow.row_factory = sqlite3.Row
        source.backup(shadow)
        shadow.execute("PRAGMA foreign_keys=ON")
        before = self._rows(shadow)
        self._connection = shadow
        self._staging = True
        try:
            with self.transaction():
                yield shadow
            after = self._rows(shadow)
        finally:
            self._connection = source
            self._staging = False
            shadow.close()
        with self.transaction() as conn:
            conn.execute("BEGIN IMMEDIATE")
            live = self._rows(conn)
            # Index progress is an independent derived cache, not world state.
            if {k:v for k,v in live.items() if k != "index_outbox"} != {k:v for k,v in before.items() if k != "index_outbox"}:
                raise RuntimeError("The save changed while resolving this turn; please retry")
            conn.execute("PRAGMA defer_foreign_keys=ON")
            for name, (columns, old) in before.items():
                new = after[name][1]
                for rowid in old.keys() - new.keys():
                    conn.execute(f'DELETE FROM "{name}" WHERE rowid=?', (rowid,))
            for name, (columns, new) in after.items():
                old = before[name][1]
                quoted = ','.join(f'"{c}"' for c in columns)
                for rowid, values in new.items():
                    if rowid not in old:
                        marks = ','.join('?' for _ in values)
                        conn.execute(f'INSERT INTO "{name}" (rowid,{quoted}) VALUES (?,{marks})', (rowid, *values))
                    elif old[rowid] != values:
                        setters = ','.join(f'"{c}"=?' for c in columns)
                        conn.execute(f'UPDATE "{name}" SET {setters} WHERE rowid=?', (*values, rowid))

    def resolve_game_scope(self, table: str, record_id: str, game_id: str | None = None) -> str | None:
        """Legacy ID-only calls are safe only with a bound game or unique row."""
        if table not in {"entities", "shops"}:
            raise ValueError("Unsupported scoped table")
        scope = game_id if game_id is not None else self.active_game_id
        if scope is not None:
            return scope
        with self.get_connection() as conn:
            rows = conn.execute(f'SELECT game_id FROM "{table}" WHERE id=?', (record_id,)).fetchall()
        if len(rows) > 1:
            raise ValueError(f"Ambiguous {table} ID; specify game_id")
        return rows[0][0] if rows else None

    def close(self) -> None:
        """Close the database connection."""
        if self._connection is not None:
            self._connection.close()
            self._connection = None
