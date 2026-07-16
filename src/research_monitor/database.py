from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, create_engine, event, inspect, select, text
from sqlalchemy.orm import Session, sessionmaker

from . import SCHEMA_VERSION
from .config import Settings
from .models import SchemaVersion
from .schema_validation import validate_current_schema
from .sqlite_backup import (
    SQLiteBackupIntegrityError,
    create_verified_sqlite_backup,
    open_sqlite_read_only,
    sqlite_integrity_check,
)
from .sqlite_files import preserve_sqlite_file_set


MIGRATIONS_PATH = Path(__file__).resolve().parent / "migrations"
ROLLBACK_JOURNAL_MAGIC = bytes.fromhex("d9d505f920a163d7")


class DatabaseCompatibilityError(RuntimeError):
    """The on-disk database cannot be used by this application version."""

    def __init__(self, found: int | str, expected: int):
        self.found = found
        self.expected = expected
        super().__init__(f"Database schema {found} is incompatible with {expected}")


class DatabaseIntegrityError(RuntimeError):
    """An existing database failed the read-only startup integrity check."""

    def __init__(self, path: Path, result: str):
        self.path = Path(path)
        self.result = result
        super().__init__(
            f"Database integrity check failed for {self.path}: {result}. "
            "Research Monitor is refusing to write to this database. Stop the "
            "application, preserve the database file for diagnosis, and restore "
            "a verified backup before restarting."
        )


class DatabaseSchemaError(RuntimeError):
    """A current-head database does not contain the required application schema."""

    def __init__(self, path: Path, detail: str):
        self.path = Path(path)
        self.detail = detail
        super().__init__(
            f"Database schema validation failed for {self.path}: {detail}"
        )


class Database:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(
            f"sqlite:///{self.path}",
            connect_args={"check_same_thread": False, "timeout": 10},
            future=True,
        )
        event.listen(self.engine, "connect", self._configure_sqlite)
        self.Session = sessionmaker(self.engine, class_=Session, expire_on_commit=False)
        self.startup_recovery: dict[str, object] | None = None

    @staticmethod
    def _configure_sqlite(dbapi_connection: sqlite3.Connection, _connection_record: object) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        # This is a single-writer application and its default data directory may
        # live on NFS. Prefer SQLite's fully durable rollback-journal policy over
        # WAL/NORMAL, whose shared-memory protocol is not safe on network filesystems.
        cursor.execute("PRAGMA synchronous=FULL")
        cursor.execute("PRAGMA busy_timeout=10000")
        cursor.close()

    def _enable_and_verify_durable_journal(self) -> None:
        """Enforce the persistent NFS-safe journal mode and verify durability.

        ``PRAGMA journal_mode=DELETE`` may change the database header when an
        older installation used WAL. Keeping it out of the connection hook lets
        initialization create a verified recovery backup before making that
        persistent change.
        """

        with self.engine.connect() as connection:
            selected = connection.exec_driver_sql("PRAGMA journal_mode=DELETE").scalar_one()
            verified = connection.exec_driver_sql("PRAGMA journal_mode").scalar_one()
            synchronous = connection.exec_driver_sql("PRAGMA synchronous").scalar_one()
            connection.commit()
        if str(selected).casefold() != "delete" or str(verified).casefold() != "delete":
            raise RuntimeError(
                "SQLite refused required DELETE journal mode "
                f"(selected={selected!r}, verified={verified!r})"
            )
        if int(synchronous) != 2:
            raise RuntimeError(
                "SQLite refused required FULL synchronous mode "
                f"(verified={synchronous!r})"
            )

    def _has_hot_rollback_journal(self) -> bool:
        journal = Path(f"{self.path}-journal")
        try:
            if journal.stat().st_size <= 512:
                return False
            with journal.open("rb") as handle:
                return handle.read(len(ROLLBACK_JOURNAL_MAGIC)) == ROLLBACK_JOURNAL_MAGIC
        except OSError:
            return False

    @staticmethod
    def _foreign_key_violations(connection: sqlite3.Connection) -> list[tuple[object, ...]]:
        return [tuple(row) for row in connection.execute("PRAGMA foreign_key_check")]

    def _recover_hot_rollback_journal(self) -> None:
        """Preserve and recover an interrupted DELETE-mode transaction."""

        reason = "Hot SQLite rollback journal detected during startup"
        try:
            preservation = preserve_sqlite_file_set(
                self.path,
                reason=reason,
                stem="startup-recovery",
            )
        except Exception as exc:
            raise DatabaseIntegrityError(
                self.path,
                f"{reason}, but raw file preservation failed: {exc}",
            ) from exc

        try:
            connection = sqlite3.connect(str(self.path), timeout=10)
            try:
                connection.execute("PRAGMA busy_timeout=10000")
                row = connection.execute("PRAGMA integrity_check").fetchone()
                result = str(row[0]) if row else "unknown"
                violations = self._foreign_key_violations(connection)
            finally:
                connection.close()
        except (OSError, sqlite3.Error) as exc:
            raise DatabaseIntegrityError(
                self.path,
                f"Rollback-journal recovery failed after preserving {preservation['directory']}: {exc}",
            ) from exc
        if result.casefold() != "ok" or violations:
            detail = result if result.casefold() != "ok" else (
                "foreign_key_check failed: " + repr(violations[:10])
            )
            raise DatabaseIntegrityError(
                self.path,
                f"Rollback-journal recovery remained invalid after preserving "
                f"{preservation['directory']}: {detail}",
            )
        self.startup_recovery = preservation

    def _verify_existing_database_integrity(self) -> None:
        """Recover an expected crash journal, then reject damaged databases."""

        if not self.path.exists():
            return
        if self._has_hot_rollback_journal():
            self._recover_hot_rollback_journal()
        try:
            result = sqlite_integrity_check(self.path)
            connection = open_sqlite_read_only(self.path)
            try:
                violations = self._foreign_key_violations(connection)
            finally:
                connection.close()
        except (OSError, sqlite3.Error) as exc:
            raise DatabaseIntegrityError(self.path, str(exc) or type(exc).__name__) from exc
        if result.casefold() != "ok":
            raise DatabaseIntegrityError(self.path, result)
        if violations:
            raise DatabaseIntegrityError(
                self.path,
                "foreign_key_check failed: " + repr(violations[:10]),
            )

    def _validate_current_schema(self) -> None:
        try:
            with self.engine.connect() as connection:
                validate_current_schema(connection)
        except DatabaseSchemaError:
            raise
        except Exception as exc:
            raise DatabaseSchemaError(
                self.path, str(exc) or type(exc).__name__,
            ) from exc

    def _alembic_config(self) -> Config:
        config = Config()
        config.set_main_option("script_location", str(MIGRATIONS_PATH))
        config.set_main_option("sqlalchemy.url", f"sqlite:///{self.path}")
        return config

    def _declared_schema_version(self) -> int | None:
        with self.engine.connect() as connection:
            if "schema_versions" not in inspect(connection).get_table_names():
                return None
            value = connection.scalar(text("SELECT max(version) FROM schema_versions"))
            if value is None:
                return None
            try:
                return int(value)
            except (TypeError, ValueError) as exc:
                raise DatabaseCompatibilityError(str(value), int(SCHEMA_VERSION)) from exc

    def _verified_recovery_backup(
        self,
        *,
        stem: str,
        suffix: str = "",
        source_integrity_error: str,
        backup_integrity_error: str,
    ) -> Path:
        if sqlite_integrity_check(self.path) != "ok":
            raise RuntimeError(source_integrity_error)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        directory = self.path.parent / "backups"
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        directory.chmod(0o700)
        target = directory / f"{stem}-{stamp}{suffix}.db"
        try:
            return create_verified_sqlite_backup(self.path, target)
        except SQLiteBackupIntegrityError as exc:
            raise RuntimeError(backup_integrity_error) from exc

    def _verified_pre_migration_backup(self, revision: str) -> Path:
        return self._verified_recovery_backup(
            stem="pre-migration",
            suffix=f"-{revision}",
            source_integrity_error="Database failed integrity check before migration",
            backup_integrity_error="Pre-migration backup failed SQLite integrity check",
        )

    def _verified_pre_journal_change_backup(self) -> Path:
        return self._verified_recovery_backup(
            stem="pre-journal-change",
            source_integrity_error="Database failed integrity check before journal change",
            backup_integrity_error=(
                "Pre-journal-change backup failed SQLite integrity check"
            ),
        )

    def initialize(self) -> None:
        # create_engine() is lazy, so this is the first database access during
        # normal startup. The helper opens the existing file read-only.
        self._verify_existing_database_integrity()

        declared_version = self._declared_schema_version()
        if declared_version is not None and declared_version != int(SCHEMA_VERSION):
            raise DatabaseCompatibilityError(declared_version, int(SCHEMA_VERSION))

        config = self._alembic_config()
        scripts = ScriptDirectory.from_config(config)
        heads = scripts.get_heads()
        if len(heads) != 1:
            raise RuntimeError("Research Monitor requires a single Alembic migration head")
        head = heads[0]
        with self.engine.connect() as connection:
            current = MigrationContext.configure(connection).get_current_revision()
            user_tables = set(inspect(connection).get_table_names()) - {"alembic_version"}
            initial_journal_mode = str(
                connection.exec_driver_sql("PRAGMA journal_mode").scalar_one()
            ).casefold()
        database_preexisted = bool(user_tables)
        durable_journal_enabled = False
        if current != head:
            pending = list(scripts.iterate_revisions(head, current or "base"))
            pending.reverse()
            for revision in pending:
                # A legacy create_all database has user tables but no Alembic
                # stamp. Fresh empty databases need no recovery copy.
                if database_preexisted:
                    self._verified_pre_migration_backup(revision.revision)
                if not durable_journal_enabled:
                    # For an existing legacy database, the backup immediately
                    # above is the recovery point preceding a WAL-to-DELETE
                    # conversion. Fresh databases already start in DELETE mode.
                    self._enable_and_verify_durable_journal()
                    durable_journal_enabled = True
                # Python's sqlite3 legacy transaction mode does not begin a
                # transaction for DDL. SQLAlchemy's ``engine.begin()`` can
                # therefore leave early CREATE/ALTER statements committed even
                # if a later migration statement fails. Emit the locking BEGIN
                # ourselves before Alembic configures its MigrationContext.
                # Alembic then recognizes this as an external transaction and
                # uses the same connection without nesting or committing it.
                with self.engine.connect() as connection:
                    connection.exec_driver_sql("BEGIN IMMEDIATE")
                    config.attributes["connection"] = connection
                    try:
                        command.upgrade(config, revision.revision)
                    except BaseException:
                        connection.rollback()
                        raise
                    else:
                        connection.commit()
                    finally:
                        config.attributes.pop("connection", None)
                with self.engine.connect() as connection:
                    user_tables = set(inspect(connection).get_table_names()) - {
                        "alembic_version"
                    }

        with self.engine.connect() as connection:
            migrated_revision = MigrationContext.configure(connection).get_current_revision()
        if migrated_revision != head:
            raise RuntimeError(
                f"Database migration stopped at {migrated_revision!r}; expected {head!r}"
            )
        self._validate_current_schema()
        if not durable_journal_enabled:
            if database_preexisted and initial_journal_mode != "delete":
                self._verified_pre_journal_change_backup()
            self._enable_and_verify_durable_journal()
        with self.Session.begin() as session:
            version = session.scalar(select(SchemaVersion).order_by(SchemaVersion.version.desc()))
            if version is None:
                session.add(SchemaVersion(version=int(SCHEMA_VERSION)))
            elif version.version != int(SCHEMA_VERSION):
                raise DatabaseCompatibilityError(version.version, int(SCHEMA_VERSION))
        # Validate the committed current schema and relational health before the
        # caller can serve requests or open an ordinary application session.
        self._validate_current_schema()
        self._verify_existing_database_integrity()

    @contextmanager
    def session(self) -> Iterator[Session]:
        with self.Session() as session:
            try:
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise

    @contextmanager
    def write_session(self) -> Iterator[Session]:
        """Serialize writers before they read a semantic base revision."""
        with self.Session() as session:
            try:
                session.execute(text("BEGIN IMMEDIATE"))
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise

    def integrity_check(self, path: Path | None = None) -> str:
        target = path or self.path
        connection = sqlite3.connect(str(target))
        try:
            row = connection.execute("PRAGMA integrity_check").fetchone()
            return str(row[0]) if row else "unknown"
        finally:
            connection.close()


_default_database: Database | None = None


def get_database(settings: Settings | None = None) -> Database:
    global _default_database
    settings = settings or Settings.load()
    if _default_database is None or _default_database.path != settings.database_path:
        # Do not publish a failed candidate as the process singleton. Otherwise
        # one incompatible-database attempt would make later commands reuse an
        # uninitialized object and silently bypass the compatibility check.
        candidate = Database(settings.database_path)
        try:
            candidate.initialize()
        except Exception:
            candidate.engine.dispose()
            raise
        _default_database = candidate
    return _default_database


def reset_database_singleton() -> None:
    global _default_database
    if _default_database is not None:
        _default_database.engine.dispose()
    _default_database = None
