"""Versioned multi-platform draft campaigns; drafting never grants publication."""

import json
from typing import Literal

from pydantic import Field, field_validator, model_validator

from services.agent_api.app.action_policy import ActionCategory
from services.agent_api.app.knowledge_library import ArtifactInput, Identifier, KnowledgeLibrary
from services.agent_api.app.memory import is_sensitive_content
from services.agent_api.app.permissions import RiskLevel
from services.agent_api.app.tools import EmptyInput, Tool, ToolInput

Platform = Literal['facebook', 'instagram', 'linkedin', 'x', 'youtube', 'tiktok', 'pinterest', 'threads', 'mastodon', 'bluesky']
PLATFORMS = ('facebook', 'instagram', 'linkedin', 'x', 'youtube', 'tiktok', 'pinterest', 'threads', 'mastodon', 'bluesky')


class SocialVariant(ToolInput):
    platform: Platform
    text: str = Field(min_length=1, max_length=12000)
    media_notes: str = Field(default='', max_length=500)

    @field_validator('text', 'media_notes')
    @classmethod
    def safe_text(cls, value):
        if is_sensitive_content(value) or any(ord(character) < 32 and character not in '\t\r\n' for character in value):
            raise ValueError('Draft contains potential credentials or invalid control characters.')
        return value


class CampaignInput(ToolInput):
    title: str = Field(min_length=1, max_length=200)
    variants: list[SocialVariant] = Field(min_length=1, max_length=10)
    sources: list[Identifier] = Field(default_factory=list, max_length=20)
    previous_id: Identifier | None = None

    @model_validator(mode='after')
    def bounded_unique_variants(self):
        if not self.title.strip() or is_sensitive_content(self.title):
            raise ValueError('Campaign title is blank or contains potential credentials.')
        if len({item.platform for item in self.variants}) != len(self.variants):
            raise ValueError('Each platform needs one exact draft variant per campaign version.')
        if any(not item.text.strip() for item in self.variants) or len(json.dumps(self.model_dump(), ensure_ascii=False).encode()) > 28000:
            raise ValueError('Campaign is blank or exceeds the combined draft limit.')
        return self


def register_social_content(registry, database, project_id):
    library = KnowledgeLibrary(registry.workspace_root, database, project_id)

    async def catalog(_):
        return {'platforms': [{'platform': name, 'draft_and_export': True,
            'publishing': ('image_url_adapter_source_unverified' if name in {'pinterest', 'instagram'} else
                           'bounded_video_upload_source_unverified' if name == 'youtube' else
                           'video_url_export_source_unverified' if name == 'tiktok' else
                           'paid_text_adapter_source_unverified' if name == 'x' else 'text_adapter_source_unverified'),
            'account_connected': 'Managed separately in Personal Connections; no account status is inferred here.'} for name in PLATFORMS],
            'boundary': 'Draft export does not validate provider limits, upload media, schedule publication or authorize paid usage.'}

    async def prepare(value):
        content = json.dumps({'format': 'usman-jarvis-social-campaign', 'version': 1,
            'state': 'draft_only', 'variants': [item.model_dump() for item in value.variants],
            'publication_authorized': False, 'provider_limits_verified': False}, ensure_ascii=False)
        result = library.create_artifact(ArtifactInput(title=value.title, kind='content', content=content,
            sources=value.sources, previous_id=value.previous_id))
        return {**result, 'publication': 'Not performed. Account, exact payload, visibility and applicable paid allowance need separate authorization.'}

    for name, schema, risk, handler, category in [
        ('social_catalog', EmptyInput, RiskLevel.LOW, catalog, ActionCategory.READ),
        ('social_prepare', CampaignInput, RiskLevel.HIGH, prepare, ActionCategory.EDIT),
    ]:
        registry.register(Tool(name, 'Prepare or inspect multi-platform draft artifacts; never publishes or uploads.', schema, risk, 10, handler,
            resolve_destination=lambda _: library.files.guard.resolve('.'), category=category))
