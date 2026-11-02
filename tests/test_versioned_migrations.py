"""Migration consent, consistent backups, complete schemas and fail-closed ledgers."""

import hashlib
import sqlite3
from contextlib import closing

import pytest

from services.agent_api.app.migrations import FOUNDATION_SQL, MIGRATIONS, initialize_database


def test_version_one_upgrade_requires_consent_and_preserves_project(tmp_path):
    path = tmp_path / "v1.sqlite"
    with closing(sqlite3.connect(path)) as db:
        db.executescript(FOUNDATION_SQL + "CREATE TABLE schema_versions(version INTEGER PRIMARY KEY,applied_at TEXT NOT NULL); INSERT INTO schema_versions VALUES(1,'fixture'); INSERT INTO projects VALUES('project','Name','root','fixture');")
    with pytest.raises(RuntimeError, match="authorization"):
        initialize_database(path)
    initialize_database(path, allow_existing=True)
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("SELECT project_id,name FROM projects").fetchone() == ("project", "Name")
        assert db.execute("SELECT version FROM schema_versions ORDER BY version").fetchall() == [(number,) for number in MIGRATIONS]
    backups = list((tmp_path / "migration_backups").glob("*.sqlite"))
    assert len(backups) == 1
    with closing(sqlite3.connect(backups[0])) as db:
        assert db.execute("SELECT MAX(version) FROM schema_versions").fetchone()[0] == 1
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_completed_foundation_upgrade_checks_only_applied_checksums_then_requires_consent(tmp_path):
    path = tmp_path / "foundation.sqlite"
    with closing(sqlite3.connect(path)) as db, db:
        db.executescript(MIGRATIONS[1] + MIGRATIONS[2])
        db.execute("CREATE TABLE schema_versions(version INTEGER PRIMARY KEY,applied_at TEXT NOT NULL)")
        for version in (1, 2):
            db.execute("INSERT INTO schema_versions VALUES(?,'fixture')", (version,))
            db.execute("INSERT INTO migration_checksums VALUES(?,?)", (version, hashlib.sha256(MIGRATIONS[version].encode()).hexdigest()))
        db.execute("INSERT INTO projects VALUES('preserved','Name','root','fixture')")
    before = path.read_bytes()
    with pytest.raises(RuntimeError, match="authorization"):
        initialize_database(path)
    assert path.read_bytes() == before
    initialize_database(path, allow_existing=True)
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("SELECT name FROM projects WHERE project_id='preserved'").fetchone()[0] == "Name"
        assert db.execute("SELECT MAX(version) FROM schema_versions").fetchone()[0] == max(MIGRATIONS)
        assert db.execute("SELECT COUNT(*) FROM coding_changes").fetchone()[0] == 0
    backup = next((tmp_path / "migration_backups").glob("*.sqlite"))
    with closing(sqlite3.connect(backup)) as db:
        assert db.execute("SELECT MAX(version) FROM schema_versions").fetchone()[0] == 2


@pytest.mark.parametrize("damage", ["missing_table", "checksum", "future", "ledger_gap"])
def test_current_database_damage_never_silently_repairs_or_downgrades(tmp_path, damage):
    path = tmp_path / "current.sqlite"
    initialize_database(path)
    with closing(sqlite3.connect(path)) as db, db:
        if damage == "missing_table":
            db.execute("DROP TABLE approval_tasks")
        elif damage == "checksum":
            db.execute("UPDATE migration_checksums SET sha256='invalid' WHERE version=1")
        elif damage == "future":
            db.execute("INSERT INTO schema_versions VALUES(99,'fixture')")
        else:
            db.execute("DELETE FROM schema_versions WHERE version=1")
    before = path.read_bytes()
    with pytest.raises(RuntimeError):
        initialize_database(path, allow_existing=True)
    assert path.read_bytes() == before


def test_checkpoint_database_upgrade_preserves_data_and_consistent_backup(tmp_path):
    path = tmp_path / "checkpoint.sqlite"
    with closing(sqlite3.connect(path)) as db, db:
        for number in (1, 2, 3):
            db.executescript(MIGRATIONS[number])
        db.execute("CREATE TABLE schema_versions(version INTEGER PRIMARY KEY,applied_at TEXT NOT NULL)")
        for number in (1, 2, 3):
            db.execute("INSERT INTO schema_versions VALUES(?,'fixture')", (number,))
            db.execute("INSERT INTO migration_checksums VALUES(?,?)", (number, hashlib.sha256(MIGRATIONS[number].encode()).hexdigest()))
        db.execute("INSERT INTO projects VALUES('preserved','Name','root','fixture')")
    before = path.read_bytes()
    with pytest.raises(RuntimeError, match="authorization"):
        initialize_database(path)
    assert path.read_bytes() == before
    initialize_database(path, allow_existing=True)
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("SELECT name FROM projects WHERE project_id='preserved'").fetchone()[0] == "Name"
        assert db.execute("SELECT COUNT(*) FROM command_runs").fetchone()[0] == 0
        assert db.execute("SELECT MAX(version) FROM schema_versions").fetchone()[0] == max(MIGRATIONS)
    backups = list((tmp_path / "migration_backups").glob("*.sqlite"))
    assert len(backups) == 1
    with closing(sqlite3.connect(backups[0])) as db:
        assert db.execute("SELECT MAX(version) FROM schema_versions").fetchone()[0] == 3
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_incompatible_legacy_schema_rolls_back_and_preserves_original(tmp_path):
    path = tmp_path / "incompatible.sqlite"
    with closing(sqlite3.connect(path)) as db:
        db.executescript("CREATE TABLE memories(id INTEGER PRIMARY KEY,content TEXT); INSERT INTO memories VALUES(1,'original data');")
    with pytest.raises(RuntimeError, match="incompatible"):
        initialize_database(path, allow_existing=True)
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("SELECT content FROM memories").fetchone()[0] == "original data"
        assert {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")} == {"memories"}
