"""Pydantic request models for the Proxima API.

Extracted verbatim from main.py (no behavior change) so the route handlers and
the request contracts live in separate, smaller files.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from .runner_specs import default_runner


class PasswordRequest(BaseModel):
    password: str


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


class AppStartRequest(BaseModel):
    command: str = Field(min_length=1)
    port: int = 5180
    dir: str = ""


class AppStatusResponse(BaseModel):
    state: Literal[
        "stopped",
        "starting",
        "ready",
        "port_conflict",
        "ownership_unknown",
        "exited",
    ]
    running: bool
    ready: bool
    requested_port: int | None = None
    port: int | None = None
    preview_port: int | None = None
    command: str | None = None
    log: list[str] = Field(default_factory=list)
    message: str | None = None
    reason: Literal["output_sink_unavailable"] | None = None
    prolonged_start: bool | None = None
    exited: bool | None = None
    exit_code: int | None = None
    broad_bind: bool | None = None


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
    profile_id: int | None = None
    input: dict[str, Any] | None = None
    title: str | None = None
    # The ONE container area this job works against (T1 job→target binding,
    # Phase-1 slice 2). A code-area target makes it a repo job (worktree +
    # diff review + local merge, behind feature_repo_worktrees); None keeps
    # today's behavior.
    target_area_id: int | None = None


class JobRunLinkRequest(BaseModel):
    run_id: int = Field(gt=0)


class JobApproveRequest(BaseModel):
    # Optional edited output to replace the just-finished step's result before the
    # workflow resumes (the "edit & continue" review action).
    edited_output: str | None = None


class JobRejectRequest(BaseModel):
    # The one-line why (slice 4, T1): rejection is a review verdict, so it must
    # leave a record - the reason lands on the job row and shows in its history.
    reason: str = Field(min_length=1, max_length=500)

    @field_validator("reason")
    @classmethod
    def _reason_not_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("a reject reason is required")
        return stripped


class GraphJobCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    graph: dict[str, Any]
    input: dict[str, Any] | None = None
    project_id: int | None = None
    project_slug: str | None = None
    profile_id: int | None = None
    workflow_id: int | None = None


class GraphDefinitionUpdateRequest(BaseModel):
    graph: dict[str, Any] | None = None
    title: str | None = Field(default=None, max_length=200)


class GraphJobStartRequest(BaseModel):
    input: dict[str, Any] | None = None


class GraphTemplateSaveRequest(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    description: str = ""
    category: str = "other"
    # Compatibility fallback for pre-trigger-input clients. New graphs declare
    # their intake fields directly on the trigger node.
    inputs: list[dict[str, Any]] = []


class GraphNodeOutputEditRequest(BaseModel):
    value: Any


class GraphScriptApproveRequest(BaseModel):
    # The owner approves BYTES, not a filename (audit F4): the approval card
    # fetched content + sha256 together, and the approve echoes that hash back
    # so the server can refuse (409) when the file changed since it was shown.
    expected_sha256: str = Field(min_length=64, max_length=64, pattern="^[0-9a-f]{64}$")


class GraphNodeAnswerRequest(BaseModel):
    answer: str = Field(min_length=1, max_length=4000)


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
    engine: str = Field(default="auto", pattern="^(auto|linear|graph)$")


class ContainerArea(BaseModel):
    id: int
    kind: str = Field(pattern="^(code|ops)$")
    rel_path: str
    source: str = Field(pattern="^(auto|manual)$")
    push_on_merge: bool = False
    push_remote_url: str | None = None
    remote: dict[str, Any] | None = None


class ContainerLiveState(BaseModel):
    running_tasks: int = Field(ge=0)
    queued_tasks: int = Field(ge=0)
    open_attention: int = Field(ge=0)


class ContainerAreaInventory(BaseModel):
    total: int = Field(ge=0)
    code: int = Field(ge=0)
    ops: int = Field(ge=0)


class ContainerHealth(BaseModel):
    registry: str = Field(pattern="^(ready|unavailable)$")
    areas: str = Field(pattern="^(ready|pending|attention)$")
    ops_migration: str
    graph_freshness: None = None


class Container(BaseModel):
    """Canonical public Container contract backed by the projects table."""

    id: int
    slug: str
    name: str
    path: str
    owner: str
    visibility: str
    identity_label: str | None = None
    summary: str | None = None
    source_hash: str | None = None
    indexed_at: str | None = None
    last_activity_at: str | None = None
    live: ContainerLiveState
    area_inventory: ContainerAreaInventory
    health: ContainerHealth


class ContainerListResponse(BaseModel):
    containers: list[Container]


class ContainerAreas(BaseModel):
    container_id: int
    container_slug: str
    code_areas: list[ContainerArea]
    ops_area: ContainerArea


class GraphScopePayload(BaseModel):
    container_id: int = Field(gt=0)
    container_slug: str
    kind: str = Field(pattern="^(knowledge|code)$")
    area_id: int | None = Field(default=None, gt=0)
    area_rel_path: str | None = None


class GraphFreshnessPayload(BaseModel):
    state: str = Field(pattern="^(missing|queued|building|fresh|stale|failed)$")
    generation: int = Field(ge=0)
    source_fingerprint: str | None = Field(
        default=None,
        pattern="^[0-9a-f]{64}$",
    )
    graph_sha256: str | None = Field(default=None, pattern="^[0-9a-f]{64}$")
    tool_version: str | None = None
    semantic_backend: str = "disabled"
    last_success_at: str | None = None
    last_attempt_at: str | None = None
    last_error: str | None = None


class GraphStatePayload(BaseModel):
    id: int = Field(gt=0)
    scope: GraphScopePayload
    state: str = Field(pattern="^(missing|queued|building|fresh|stale|failed)$")
    generation: int = Field(ge=0)
    freshness: GraphFreshnessPayload


class GraphStatesResponse(BaseModel):
    graphs: list[GraphStatePayload]


class GraphRebuildRequest(BaseModel):
    kind: str = Field(pattern="^(knowledge|code)$")
    area_id: int | None = Field(default=None, gt=0)


class ContainerUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)


class ProjectUpdateRequest(ContainerUpdateRequest):
    """Compatibility request name for existing project routes."""


class ContainerCreateRequest(BaseModel):
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,62}[a-z0-9]$|^[a-z0-9]$")
    name: str = Field(min_length=1, max_length=120)

    @field_validator("name")
    @classmethod
    def _name_not_blank(cls, v: str) -> str:
        # min_length counts whitespace, so "   " slips through — reject blank names.
        if not v.strip():
            raise ValueError("name must not be blank")
        return v


class ProjectCreateRequest(ContainerCreateRequest):
    """Compatibility request name for existing project routes."""


class ContainerLinkRequest(BaseModel):
    path: str = Field(min_length=1)
    root_id: str = Field(min_length=1, max_length=64)
    name: str | None = Field(default=None, max_length=120)
    slug: str | None = None
    # When true, create `path` as a new empty directory under an existing parent
    # (inside link roots) and then register it. Never deletes or moves content.
    mkdir: bool = False


class ProjectLinkRequest(ContainerLinkRequest):
    """Compatibility request name for existing project routes."""


class ProjectAreaAddRequest(BaseModel):
    rel_path: str = Field(min_length=1, max_length=500)  # '.' = container root


class ProjectAreaUpdateRequest(BaseModel):
    # T9 (slice 11): the per-code-area push-after-merge opt-in. Enabling
    # requires a detected git remote (no remote -> the toggle is not offered).
    push_on_merge: bool


class CommandRequest(BaseModel):
    command: str = Field(min_length=1)
    project_slug: str | None = None
    runner_id: str | None = None
    profile_id: int | None = None


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
    # Master sessions are server-created through the dedicated Master surface.
    # Alpha remains a one-release legacy payload reader.
    mode: str = Field(default="chat", pattern="^(chat|design|master|alpha)$")


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
    # Media-prompt routing (/image, /design) resolves the project from the session;
    # this override covers brand-new sessions relayed from /api/chat/send.
    project_slug: str | None = None


class GoalRequest(BaseModel):
    objective: str = Field(min_length=1)
    profile_id: int | None = None
    model: str | None = None
    max_iter: int = Field(default=20, ge=1, le=100)


class MessageReviewCreateRequest(BaseModel):
    reviewer_profile_id: int | None = None


class MessageReviewAskOriginalRequest(BaseModel):
    note: str | None = None


class ImageGenRequest(BaseModel):
    prompt: str = Field(min_length=1)
    size: str = "1024x1024"
    model: str | None = None
    image: str | None = (
        None  # relative project path of an existing asset, for edit/manipulate
    )
    # Multiple source/reference images (relative project paths) to edit/compose into
    # one — providers that advertise referenceImages (e.g. codex) honour all of them;
    # single-image providers use the first. Takes precedence over `image` when set.
    images: list[str] | None = None


class WikiDraftRequest(BaseModel):
    profile_id: int | None = None


class WikiCommitRequest(BaseModel):
    path: str
    content: str
    mode: str = "new"  # 'new' | 'append' | 'overwrite'


class ChatSendRequest(BaseModel):
    session_id: int | None = None
    message: str = Field(min_length=1)
    project_slug: str | None = None
    profile_id: int | None = None
    runner_id: str = Field(default_factory=default_runner)
    model: str | None = None


class ArchiveStatusRequest(BaseModel):
    status: str = Field(min_length=1)  # draft | review | approved | superseded


class FileWriteRequest(BaseModel):
    content: str


class FsPathRequest(BaseModel):
    path: str = Field(min_length=1)
    target: dict[str, Any] | None = None


class FsRenameRequest(BaseModel):
    from_: str = Field(min_length=1, alias="from")
    to: str = Field(min_length=1)
    from_target: dict[str, Any] | None = None
    to_target: dict[str, Any] | None = None

    model_config = {"populate_by_name": True}
