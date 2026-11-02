"""Versioned, transactional schema changes with opt-in and consistent legacy backups."""

import hashlib
import sqlite3
import uuid
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from services.agent_api.app.legacy_schema import LEGACY_SQL

FOUNDATION_SQL = """
CREATE TABLE IF NOT EXISTS projects (project_id TEXT PRIMARY KEY, name TEXT NOT NULL, root TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS cloud_policies (endpoint TEXT NOT NULL, model TEXT NOT NULL, policy_json TEXT NOT NULL, PRIMARY KEY(endpoint,model));
CREATE TABLE IF NOT EXISTS model_reservations (reservation_id TEXT PRIMARY KEY, day TEXT NOT NULL, task_id TEXT NOT NULL, endpoint TEXT NOT NULL, model TEXT NOT NULL, reserved_micro_usd INTEGER NOT NULL CHECK(reserved_micro_usd>0));
"""
SCOPE_SQL = """
CREATE TABLE IF NOT EXISTS project_memories (id INTEGER PRIMARY KEY, project_id TEXT NOT NULL, memory_type TEXT NOT NULL, content TEXT NOT NULL, importance INTEGER, source TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS task_scopes (task_id TEXT PRIMARY KEY, project_id TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS event_scopes (event_id INTEGER PRIMARY KEY, project_id TEXT, task_id TEXT);
CREATE TABLE IF NOT EXISTS task_inputs (task_id TEXT PRIMARY KEY, memory_ids_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS action_policy (id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER NOT NULL, blocked_json TEXT NOT NULL, read_approval INTEGER NOT NULL);
INSERT OR IGNORE INTO action_policy VALUES (1,1,'[]',0);
CREATE TABLE IF NOT EXISTS migration_checksums (version INTEGER PRIMARY KEY, sha256 TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS reservations_task ON model_reservations(task_id);
CREATE INDEX IF NOT EXISTS reservations_day ON model_reservations(day);
CREATE INDEX IF NOT EXISTS memories_project ON project_memories(project_id);
"""
CODING_SQL = """
CREATE TABLE IF NOT EXISTS coding_changes (change_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, root TEXT NOT NULL, path TEXT NOT NULL, before_sha TEXT NOT NULL, before_content BLOB NOT NULL, after_sha TEXT NOT NULL, after_content BLOB NOT NULL, proposal_sha TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL);
"""
COMMAND_SQL = """
CREATE TABLE IF NOT EXISTS command_runs (run_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, root TEXT NOT NULL, contract_json TEXT NOT NULL, contract_sha TEXT NOT NULL, status TEXT NOT NULL, result_json TEXT, created_at TEXT NOT NULL);
"""
CREATE_SQL = """
CREATE TABLE IF NOT EXISTS coding_created_files (change_id TEXT PRIMARY KEY, device TEXT, inode TEXT);
"""
WORKSPACE_SQL = """
CREATE TABLE IF NOT EXISTS runtime_flags (name TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS agent_jobs (job_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, root TEXT NOT NULL, role TEXT NOT NULL, input_json TEXT NOT NULL, dependencies_json TEXT NOT NULL, paths_json TEXT NOT NULL, status TEXT NOT NULL, result_json TEXT, approval_id TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS agent_file_claims (project_id TEXT NOT NULL, path TEXT NOT NULL, job_id TEXT NOT NULL, PRIMARY KEY(project_id,path));
CREATE TABLE IF NOT EXISTS knowledge_documents (document_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, root TEXT NOT NULL, title TEXT NOT NULL, source_path TEXT NOT NULL, source_sha TEXT NOT NULL, content TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS creation_artifacts (artifact_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, root TEXT NOT NULL, title TEXT NOT NULL, kind TEXT NOT NULL, content TEXT NOT NULL, sources_json TEXT NOT NULL, version INTEGER NOT NULL, previous_id TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS decision_items (decision_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, task_id TEXT, question TEXT NOT NULL, status TEXT NOT NULL, answer TEXT, created_at TEXT NOT NULL, answered_at TEXT);
CREATE TABLE IF NOT EXISTS durable_schedules (schedule_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, title TEXT NOT NULL, kind TEXT NOT NULL, payload_json TEXT NOT NULL, next_due TEXT NOT NULL, interval_seconds INTEGER, status TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS schedule_occurrences (occurrence_id TEXT PRIMARY KEY, schedule_id TEXT NOT NULL, project_id TEXT NOT NULL, due_at TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(schedule_id,due_at));
CREATE TABLE IF NOT EXISTS work_records (record_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, kind TEXT NOT NULL, title TEXT NOT NULL, content TEXT NOT NULL, status TEXT NOT NULL, revision INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS installed_skill_manifests (skill_id TEXT NOT NULL, project_id TEXT NOT NULL, root TEXT NOT NULL, version TEXT NOT NULL, manifest_json TEXT NOT NULL, enabled INTEGER NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(project_id,skill_id));
CREATE TABLE IF NOT EXISTS connected_accounts (account_id TEXT PRIMARY KEY, provider TEXT NOT NULL, label TEXT NOT NULL, secret_reference TEXT NOT NULL, scopes_json TEXT NOT NULL, revision INTEGER NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS account_actions (operation_id TEXT PRIMARY KEY, account_id TEXT NOT NULL, arguments_sha TEXT NOT NULL, status TEXT NOT NULL, receipt_json TEXT, created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS agent_jobs_project ON agent_jobs(project_id,created_at);
CREATE INDEX IF NOT EXISTS documents_project ON knowledge_documents(project_id,created_at);
CREATE INDEX IF NOT EXISTS artifacts_project ON creation_artifacts(project_id,created_at);
CREATE INDEX IF NOT EXISTS schedules_due ON durable_schedules(status,next_due);
CREATE INDEX IF NOT EXISTS decisions_project ON decision_items(project_id,status);
"""
COORDINATION_SQL = """
CREATE TABLE IF NOT EXISTS agent_batches (batch_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, root TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS agent_batch_jobs (batch_id TEXT NOT NULL, job_id TEXT NOT NULL UNIQUE, position INTEGER NOT NULL, PRIMARY KEY(batch_id,position));
"""
OAUTH_SQL = """
CREATE TABLE IF NOT EXISTS oauth_clients (provider TEXT PRIMARY KEY, client_id TEXT NOT NULL, secret_reference TEXT, revision INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS oauth_account_tokens (account_id TEXT PRIMARY KEY, provider TEXT NOT NULL, client_revision INTEGER NOT NULL, refresh_reference TEXT, expires_at TEXT, state TEXT NOT NULL);
"""
DRAFT_SQL = """
CREATE TABLE IF NOT EXISTS agent_drafts (workspace_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, root TEXT NOT NULL, revision INTEGER NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS agent_draft_files (workspace_id TEXT NOT NULL, path TEXT NOT NULL, base_sha TEXT NOT NULL, base_content TEXT NOT NULL, content TEXT NOT NULL, PRIMARY KEY(workspace_id,path));
CREATE INDEX IF NOT EXISTS drafts_project ON agent_drafts(project_id,created_at);
"""
UIA_SQL = """
CREATE TABLE IF NOT EXISTS owned_uia_operations (operation_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, action TEXT NOT NULL, contract_sha TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
"""
ACCOUNT_SERVICE_SQL = """
CREATE TABLE IF NOT EXISTS account_services (account_id TEXT PRIMARY KEY, base_url TEXT NOT NULL);
"""
MATERIALIZATION_SQL = """
CREATE TABLE IF NOT EXISTS draft_materializations (materialization_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, root TEXT NOT NULL, workspace_id TEXT NOT NULL, revision INTEGER NOT NULL, contract_json TEXT NOT NULL, contract_sha TEXT NOT NULL, status TEXT NOT NULL, device TEXT, inode TEXT, created_at TEXT NOT NULL);
"""
PUBLISHING_BUDGET_SQL = """
CREATE TABLE IF NOT EXISTS publishing_budgets (account_id TEXT PRIMARY KEY, revision INTEGER NOT NULL, enabled INTEGER NOT NULL, unit_micro_usd INTEGER NOT NULL, daily_micro_usd INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS publishing_reservations (operation_id TEXT PRIMARY KEY, account_id TEXT NOT NULL, day TEXT NOT NULL, micro_usd INTEGER NOT NULL CHECK(micro_usd>0));
"""
MIGRATIONS = {1: FOUNDATION_SQL, 2: LEGACY_SQL + SCOPE_SQL, 3: CODING_SQL, 4: COMMAND_SQL, 5: CREATE_SQL, 6: WORKSPACE_SQL, 7: COORDINATION_SQL, 8: OAUTH_SQL, 9: DRAFT_SQL, 10: UIA_SQL, 11: ACCOUNT_SERVICE_SQL, 12: MATERIALIZATION_SQL, 13: PUBLISHING_BUDGET_SQL}


def initialize_database(path: Path, *, allow_existing: bool = False):
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        version = (connection.execute("SELECT MAX(version) FROM schema_versions").fetchone()[0]
                   if "schema_versions" in tables else 0) or 0
        if version > max(MIGRATIONS):
            raise RuntimeError("Database requires a newer JARVIS backend.")
        if "schema_versions" in tables:
            versions = [row[0] for row in connection.execute("SELECT version FROM schema_versions ORDER BY version")]
            if versions != list(range(1, version + 1)):
                raise RuntimeError("Database migration ledger is incomplete; inspect a backup before recovery.")
        if version >= 2:
            if "migration_checksums" not in tables:
                raise RuntimeError("Database migration ledger is incomplete; inspect a backup before recovery.")
            recorded = dict(connection.execute("SELECT version,sha256 FROM migration_checksums"))
            for number, sql in MIGRATIONS.items():
                if number <= version and recorded.get(number) != hashlib.sha256(sql.encode()).hexdigest():
                    raise RuntimeError("Database migration checksum mismatch; inspect a backup before recovery.")
        pending = [number for number in MIGRATIONS if number > version]
        if not pending:
            _verify_schema(connection)
            return
        if tables and not allow_existing:
            raise RuntimeError("Existing database migration requires explicit authorization. Set JARVIS_ALLOW_DATABASE_MIGRATION=1 only for an approved migration.")
        if tables:
            backup_dir = path.parent / "migration_backups"
            backup_dir.mkdir(exist_ok=True)
            with closing(sqlite3.connect(backup_dir / (path.name + "." + uuid.uuid4().hex + ".sqlite"))) as backup:
                connection.backup(backup)
        with connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("CREATE TABLE IF NOT EXISTS schema_versions (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
            for number in pending:
                for statement in MIGRATIONS[number].split(";"):
                    if statement.strip():
                        connection.execute(statement)
                connection.execute("INSERT INTO schema_versions VALUES (?,?)", (number, datetime.now(UTC).isoformat()))
            for number, sql in MIGRATIONS.items():
                connection.execute("INSERT OR IGNORE INTO migration_checksums VALUES (?,?)", (number, hashlib.sha256(sql.encode()).hexdigest()))
            _verify_schema(connection)


def _verify_schema(connection):
    # Derive the expected column contract from the immutable migration sources.
    # A version number alone must never make an incomplete database usable.
    with closing(sqlite3.connect(":memory:")) as expected:
        for sql in MIGRATIONS.values():
            expected.executescript(sql)
        for (table,) in expected.execute("SELECT name FROM sqlite_master WHERE type='table'"):
            if table == "sqlite_sequence":
                continue
            expected_columns = [tuple(row[1:]) for row in expected.execute(f"PRAGMA table_info({table})")]
            present = [tuple(row[1:]) for row in connection.execute(f"PRAGMA table_info({table})")]
            if expected_columns != present:
                raise RuntimeError("Database schema is incomplete or incompatible; migration rolled back. Recover from a verified backup.")
