"""Scoped task services and memory. Personal records never enter project context."""

import os
import sqlite3
from contextlib import closing, contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from pathlib import Path

from services.agent_api.app.capabilities import (
    CapabilityRegistry,
    CapabilityResolver,
    SkillRegistry,
)
from services.agent_api.app.context_builder import ContextBuilder
from services.agent_api.app.memory import Memory, MemoryRepository
from services.agent_api.app.model_planner import ModelPlanner
from services.agent_api.app.orchestrator import AgentOrchestrator
from services.agent_api.app.planner import DeterministicPlanner
from services.agent_api.app.tools import ToolExecutor, build_registry

execution_project: ContextVar[str | None] = ContextVar("execution_project", default=None)
MEMORY_COLUMNS = "id,memory_type,content,importance,source,created_at,updated_at"


class ProjectMemory(MemoryRepository):
    def __init__(self, path: Path, project_id: str):
        super().__init__(path)
        self.project_id = project_id

    def create(self, memory_type, content, importance, source):
        self._reject_sensitive_content(content)
        now = datetime.now(UTC).isoformat()
        with closing(sqlite3.connect(self.database_path)) as db, db:
            cursor = db.execute("INSERT INTO project_memories VALUES (NULL,?,?,?,?,?,?,?)",
                                (self.project_id, memory_type, content, importance, source, now, now))
            row = db.execute(f"SELECT {MEMORY_COLUMNS} FROM project_memories WHERE id=? AND project_id=?", (cursor.lastrowid, self.project_id)).fetchone()
        return Memory(*row)

    def get(self, memory_id):
        with closing(sqlite3.connect(self.database_path)) as db:
            row = db.execute(f"SELECT {MEMORY_COLUMNS} FROM project_memories WHERE id=? AND project_id=?", (memory_id, self.project_id)).fetchone()
        if row is None:
            raise KeyError(memory_id)
        return Memory(*row)

    def search(self, query="", memory_type=None):
        sql = f"SELECT {MEMORY_COLUMNS} FROM project_memories WHERE project_id=? AND content LIKE ?"
        parameters = [self.project_id, f"%{query}%"]
        if memory_type:
            sql += " AND memory_type=?"
            parameters.append(memory_type)
        with closing(sqlite3.connect(self.database_path)) as db:
            return [Memory(*row) for row in db.execute(sql + " ORDER BY updated_at DESC LIMIT 100", parameters)]

    def update(self, memory_id, content):
        self._reject_sensitive_content(content)
        with closing(sqlite3.connect(self.database_path)) as db, db:
            if db.execute("UPDATE project_memories SET content=?,updated_at=? WHERE id=? AND project_id=?", (content, datetime.now(UTC).isoformat(), memory_id, self.project_id)).rowcount != 1:
                raise KeyError(memory_id)
        return self.get(memory_id)

    def delete(self, memory_id):
        with closing(sqlite3.connect(self.database_path)) as db, db:
            if db.execute("DELETE FROM project_memories WHERE id=? AND project_id=?", (memory_id, self.project_id)).rowcount != 1:
                raise KeyError(memory_id)


def project_root(store, project_id: str) -> Path:
    with closing(sqlite3.connect(store.path)) as db:
        row = db.execute("SELECT root FROM projects WHERE project_id=?", (project_id,)).fetchone()
    if row is None:
        raise KeyError(project_id)
    expected = row[0]
    try:
        root = Path(expected).resolve(strict=True)
    except OSError as exc:
        raise ValueError("Project directory is unavailable.") from exc
    if not root.is_dir() or os.path.normcase(str(root)) != expected:
        raise ValueError("Project root changed; register and approve the new scope.")
    return root


@contextmanager
def project_scope(project_id):
    token = execution_project.set(project_id)
    try:
        yield
    finally:
        execution_project.reset(token)


class ProjectRuntime:
    def __init__(self, app, project_id: str):
        self.project_id = project_id
        self.root = project_root(app.state.foundation, project_id)
        self.memory = ProjectMemory(app.state.settings.database_path, project_id)
        registry = build_registry(self.root)
        from services.agent_api.app.project_files import register_project_files
        register_project_files(registry)
        from services.agent_api.app.coding_changes import register_coding_changes
        register_coding_changes(registry, self.memory.database_path, project_id, app.state.provider, app.state.settings.ai_timeout_seconds)
        from services.agent_api.app.studio_commands import register_studio_commands
        register_studio_commands(registry, self.memory.database_path, project_id)
        from services.agent_api.app.project_model_context import register_project_chat
        register_project_chat(registry, self.memory.database_path, project_id, app.state.provider, app.state.settings.ai_timeout_seconds)
        from services.agent_api.app.knowledge_library import register_knowledge_tools
        register_knowledge_tools(registry, self.memory.database_path, project_id)
        from services.agent_api.app.research_sources import register_research_tool
        register_research_tool(registry, self.memory.database_path, project_id)
        from services.agent_api.app.agent_drafts import register_agent_drafts
        register_agent_drafts(registry, self.memory.database_path, project_id, app.state.provider, app.state.settings.ai_timeout_seconds)
        from services.agent_api.app.draft_materializations import register_draft_materializations
        register_draft_materializations(registry, self.memory.database_path, project_id)
        from services.agent_api.app.social_content import register_social_content
        register_social_content(registry, self.memory.database_path, project_id)
        from services.agent_api.app.skill_manifests import register_lifecycle_tools
        register_lifecycle_tools(registry, self, app)
        self.executor = ToolExecutor(registry, self.memory)
        self.context = ContextBuilder(self.memory, registry, project_id=project_id, instructions=self.instructions)
        capabilities = CapabilityRegistry.from_tools(registry)
        self.capabilities = capabilities
        self.skills = SkillRegistry(capabilities)
        from services.agent_api.app.skill_manifests import load as load_skill_manifests
        load_skill_manifests(self)
        planner = ModelPlanner(self.context, app.state.brain_provider, registry, DeterministicPlanner(), app.state.settings.ai_timeout_seconds)
        self.orchestrator = AgentOrchestrator(self.executor, self.memory, planner, CapabilityResolver(capabilities, self.skills))

    def instructions(self):
        from services.agent_api.app.action_policy import ActionCategory
        from services.agent_api.app.project_files import ProjectFiles

        if not self.executor.policy.allowed(ActionCategory.READ):
            return ""
        try:
            return ProjectFiles(self.root).read("AGENTS.md", 4000)["content"]
        except (ValueError, OSError, UnicodeError):
            return ""


def runtime_for(app, project_id: str | None):
    if project_id is None:
        return app.state
    root = project_root(app.state.foundation, project_id)
    cached = app.state.project_runtimes.get(project_id)
    if cached is None or cached.root != root:
        cached = ProjectRuntime(app, project_id)
        app.state.project_runtimes[project_id] = cached
    return cached


def task_project(path, task_id):
    with closing(sqlite3.connect(path)) as db:
        row = db.execute("SELECT project_id FROM task_scopes WHERE task_id=?", (task_id,)).fetchone()
    return row[0] if row else None
