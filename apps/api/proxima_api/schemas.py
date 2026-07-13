"""Pydantic request models for the Proxima API.

Extracted verbatim from main.py (no behavior change) so the route handlers and
the request contracts live in separate, smaller files.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator

from .runner_specs import default_runner


class LoginRequest(BaseModel):
    username: str
    password: str = "password123"


class PasswordRequest(BaseModel):
    password: str


class SharedProjectSpec(BaseModel):
    # Pattern enforced at the request layer so an invalid slug fails as 422 BEFORE
    # setup_bootstrap performs any DB writes (prevents a half-bootstrapped admin).
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,62}[a-z0-9]$|^[a-z0-9]$")
    name: str | None = Field(default=None, max_length=120)


class BootstrapRequest(BaseModel):
    username: str = Field(min_length=1)
    password: str = Field(min_length=8)
    profile_name: str = "Default"
    profile_slug: str = "default"
    runner_id: str = Field(default_factory=default_runner)
    team_name: str | None = Field(default=None, max_length=80)
    shared_project: SharedProjectSpec | None = None


class UserCreateRequest(BaseModel):
    username: str = Field(min_length=1)
    password: str = Field(min_length=8)
    role: str = "member"
    profile_name: str = "Default"
    profile_slug: str = "default"


class UserUpdateRequest(BaseModel):
    role: str | None = Field(default=None, pattern=r"^(environment_admin|member)$")
    password: str | None = Field(default=None, min_length=8)


class PasswordChangeRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8)


class InviteCreateRequest(BaseModel):
    role: str = Field(default="member", pattern="^(member|admin)$")
    expires_in_hours: int = Field(default=168, ge=1, le=24 * 90)


class InviteRedeemRequest(BaseModel):
    username: str = Field(min_length=1)
    password: str = Field(min_length=8)
    profile_name: str = "Default"
    profile_slug: str = "default"


class AppStartRequest(BaseModel):
    command: str = Field(min_length=1)
    port: int = 5180
    dir: str = ""


class PermissionResponse(BaseModel):
    request_id: str
    option_id: str


class WorkflowCreateRequest(BaseModel):
    name: str = Field(min_length=1)
    description: str = ""
    category: str = "other"
    project_id: int | None = None
    project_slug: str | None = None
    steps: list[dict[str, Any]] = []
    inputs: list[dict[str, Any]] = []


class WorkflowUpdateRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    category: str | None = None
    status: str | None = Field(default=None, pattern="^(active|draft|archived)$")
    steps: list[dict[str, Any]] | None = None
    inputs: list[dict[str, Any]] | None = None


class JobCreateRequest(BaseModel):
    workflow_id: int | None = None
    project_id: int | None = None
    project_slug: str | None = None
    input: dict[str, Any] | None = None
    title: str | None = None


class JobApproveRequest(BaseModel):
    # Optional edited output to replace the just-finished step's result before the
    # workflow resumes (the "edit & continue" review action).
    edited_output: str | None = None


class ScheduleCreateRequest(BaseModel):
    workflow_id: int
    cron: str = Field(min_length=1)
    input: dict[str, Any] | None = None
    overlap_policy: str = Field(default="skip", pattern="^(skip|allow)$")
    project_id: int | None = None
    enabled: bool = True


class ScheduleUpdateRequest(BaseModel):
    cron: str | None = None
    input: dict[str, Any] | None = None
    overlap_policy: str | None = Field(default=None, pattern="^(skip|allow)$")
    enabled: bool | None = None


class PromoteWorkflowRequest(BaseModel):
    profile_id: int | None = None


class ProjectVisibilityRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    visibility: str | None = Field(default=None, pattern="^(private|shared)$")


class ProjectCreateRequest(BaseModel):
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,62}[a-z0-9]$|^[a-z0-9]$")
    name: str = Field(min_length=1, max_length=120)
    visibility: str = Field(default="private", pattern="^(private|shared)$")

    @field_validator("name")
    @classmethod
    def _name_not_blank(cls, v: str) -> str:
        # min_length counts whitespace, so "   " slips through — reject blank names.
        if not v.strip():
            raise ValueError("name must not be blank")
        return v


class ProjectLinkRequest(BaseModel):
    path: str = Field(min_length=1)
    name: str | None = Field(default=None, max_length=120)
    slug: str | None = None


class MemberRequest(BaseModel):
    username: str = Field(min_length=1)


class CommandRequest(BaseModel):
    command: str = Field(min_length=1)
    project_slug: str | None = None
    runner_id: str | None = None


class ProfileCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    runner_id: str = Field(default_factory=default_runner)
    instructions: str | None = None  # per-profile agent instructions (soul/AGENTS.md)


class ProfileUpdateRequest(BaseModel):
    name: str | None = None
    default_model: str | None = None
    is_default: bool | None = None
    runner_id: str | None = None
    instructions: str | None = None
    # Per-profile skill/MCP selection. {"skills":[ids],"mcp":[names]} to override;
    # null leaves it unchanged (send {} to enable nothing, omit to not touch).
    capabilities: dict[str, Any] | None = None


class SessionCreateRequest(BaseModel):
    title: str | None = None
    project_slug: str | None = None
    profile_id: int | None = None
    runner_id: str = Field(default_factory=default_runner)
    visibility: str = Field(default="private", pattern="^(private|project)$")
    # 'chat' (default) or 'design' — a design session is created by Design Studio so
    # the UI can route it back there (never render its scene JSON in the main chat).
    mode: str = Field(default="chat", pattern="^(chat|design)$")


class SessionUpdateRequest(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    # Reassign the chat to a project (slug) or detach it (null). Only applied when
    # the field is explicitly present in the request — see model_fields_set below.
    project_slug: str | None = None
    # Switch which agent profile runs this chat, persisted so the choice survives reload.
    profile_id: int | None = None


class MessageCreateRequest(BaseModel):
    role: str = Field(pattern="^(user|system|assistant)$")
    content: str = Field(min_length=1)


class RunCreateRequest(BaseModel):
    message: str = Field(min_length=1)
    display_message: str | None = Field(default=None, min_length=1)
    instant_result: str | None = Field(default=None, min_length=1)
    profile_id: int | None = None
    participant_profile_ids: list[int] | None = None
    model: str | None = None
    prompt_mode: str = Field(default="chat", pattern="^(chat|brainstorm|debate)$")
    # Media-prompt routing (/image, /video) resolves the project from the session;
    # this override covers brand-new sessions relayed from /api/chat/send.
    project_slug: str | None = None


class GoalRequest(BaseModel):
    objective: str = Field(min_length=1)
    profile_id: int | None = None
    model: str | None = None
    max_iter: int = Field(default=20, ge=1, le=100)


class MessageReviewCreateRequest(BaseModel):
    mode: str = Field(default="validate", pattern="^(validate|brainstorm|debate|compare)$")
    reviewer_profile_id: int | None = None


class MessageReviewAskOriginalRequest(BaseModel):
    note: str | None = None


class ImageGenRequest(BaseModel):
    prompt: str = Field(min_length=1)
    size: str = "1024x1024"
    model: str | None = None
    image: str | None = None  # relative project path of an existing asset, for edit/manipulate


class WikiDraftRequest(BaseModel):
    profile_id: int | None = None


class WikiCommitRequest(BaseModel):
    path: str
    content: str
    mode: str = "new"   # 'new' | 'append' | 'overwrite'


class ChatSendRequest(BaseModel):
    session_id: int | None = None
    message: str = Field(min_length=1)
    project_slug: str | None = None
    profile_id: int | None = None
    runner_id: str = Field(default_factory=default_runner)
    model: str | None = None


class FileWriteRequest(BaseModel):
    content: str


class FsPathRequest(BaseModel):
    path: str = Field(min_length=1)


class FsRenameRequest(BaseModel):
    from_: str = Field(min_length=1, alias="from")
    to: str = Field(min_length=1)

    model_config = {"populate_by_name": True}
