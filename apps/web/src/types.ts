export type User = {
	id: number;
	username: string;
	role: string;
	os_user: string;
};
export type Profile = {
	id: number;
	slug: string;
	name: string;
	default_model?: string | null;
	is_default: boolean;
	hermes_home?: string;
	runner_id?: string;
	instructions?: string | null;
	capabilities?: CapabilitySelection | null;
};
export type CapabilitySelection = { skills?: string[]; mcp?: string[] };
export type DetectedSkill = { id: string; name: string; description?: string; source?: string; group?: string };
export type DetectedMcp = { name: string; kind: string; detail?: string };
export type RunnerCapabilities = { runner_id: string; skills: DetectedSkill[]; mcp: DetectedMcp[] };
export type Project = {
	slug: string;
	name: string;
	path: string;
	owner: string;
	role: string;
	visibility: "private" | "shared";
};
// The project's work-container areas (T1, slice 1): git-repo subfolders as code
// areas ("." = the root itself) plus the single ops area for everything else.
export type ProjectAreas = {
	code_areas: { id: number; rel_path: string; source: string }[];
	ops_area: { id: number; rel_path: string } | null;
};
export type Runner = {
	id: string;
	displayName: string;
	installed: boolean;
	path?: string | null;
	binary?: string | null;
	hasAdapter: boolean;
	detectionOnly: boolean;
	runnable: boolean;
	notes?: string;
};
export type AppFeatures = {
	designStudio: boolean;
	workflowGraph: boolean;
};
export type ChatSession = {
	id: number;
	title: string;
	runner_id: string;
	profile_id?: number | null;
	profile_slug?: string | null;
	profile_name?: string | null;
	project_slug?: string | null;
	project_name?: string | null;
	visibility: "private" | "project";
	updated_at?: string;
	job_id?: number | null;
	workflow_id?: number | null;
	mode?: "chat" | "design" | string;
};
export type ActivityItem = { title: string; status: string; subagent: boolean };
export type OutputLink = {
	type:
		| "design"
		| "video-file"
		| "app"
		| "page"
		| "doc"
		| "file"
		| "image"
		| string;
	title: string;
	path: string;
	id?: string;
	dir?: string;
	command?: string;
	project_slug?: string | null;
};
export type MessageReview = {
	id: number;
	source_message_id: number;
	session_id: number;
	run_id?: number | null;
	mode: "validate" | "brainstorm" | "debate" | "compare" | string;
	status: "queued" | "running" | "done" | "failed" | string;
	source_runner?: string | null;
	source_profile_id?: number | null;
	reviewer_profile_id?: number | null;
	reviewer_profiles: { id: number; name: string; runner_id: string }[];
	verdict?: string | null;
	gaps: string[];
	depends_on_input: string[];
	revised_content?: string | null;
	suggested_next_move?: string | null;
	raw_transcript?: string | null;
	merge_transcript?: string | null;
	source_original_content?: string | null;
	applied_at?: string | null;
	error?: string | null;
	created_at?: string;
	updated_at?: string;
};
export type ChatMessage = {
	id?: number;
	role: "user" | "system" | "assistant" | "error";
	content: string;
	author?: string | null;
	run_id?: number | null;
	activity?: ActivityItem[];
	output_links?: OutputLink[];
	created_at?: string;
	duration_s?: number;
};
// Autonomous goal loop state for a session.
export type GoalState = {
	objective: string;
	status: "running" | "done" | "blocked" | "capped" | "cancelled" | string;
	iteration: number;
	max: number;
};
export type RunEvent = {
	id: number;
	seq: number;
	type: string;
	run_id: number;
	session_id: number;
	project_id?: number | null;
	payload: Record<string, unknown>;
	created_at: string;
};
export type WikiDraft = {
	title: string;
	path: string;
	body: string;
	related: string[];
	conflicts: string[];
	action: "new" | "merge";
	target?: string | null;
	unparsed?: boolean;
};
// A declared workflow input — collected in the Run modal and used to fill
// {{id}} placeholders in step text on the backend.
export type WorkflowInput = {
	id: string;
	label: string;
	kind: "text" | "url" | "number" | "file";
	required: boolean;
};
// A reusable recipe step. When creating a workflow only {name, instruction,
// expected_output?, type?} need to be sent; the server fills in the rest.
export type WorkflowStep = {
	id: string;
	name: string;
	instruction: string;
	expected_output: string;
	type: string;
	rules: string | null;
	skill_ids: string[] | null;
	review_required: boolean;
	depends_on: string[] | null;
};
export type Workflow = {
	id: number;
	project_id: number | null;
	name: string;
	description: string;
	category: string;
	status: "active" | "draft" | "archived";
	inputs: WorkflowInput[];
	steps: WorkflowStep[];
	created_by: number | null;
	created_at: string;
	updated_at: string;
};
// A draft promoted from a chat — opens straight into the editor (unsaved).
export type WorkflowDraft = {
	name: string;
	description?: string;
	category?: string;
	inputs?: WorkflowInput[];
	steps: {
		name: string;
		instruction: string;
		expected_output?: string;
		type?: string;
	}[];
};

export type GraphOutputKind = "text" | "json" | "artifact-ref";

// "script" (slice 6, T6) is a deterministic step: it runs a saved script from the
// project's scripts/ library — no LLM, no agent — under the same node state machine.
export type GraphNodeType = "agent" | "trigger" | "script";
// Only manual entry exists today; schedule/webhook/event become further kinds of
// this same node rather than a separate execution path.
export type GraphTriggerKind = "manual";

export type GraphNodeDefinition = {
	id: string;
	// Absent on graphs authored before node types existed; those are agent nodes.
	type?: GraphNodeType;
	name: string;
	instruction: string;
	// What a good result is, and the constraints on how to get there — the detail a
	// linear recipe step carried. Prose for the runner; output_kind/output_schema
	// stay the enforced contract. Absent when blank: blank is not a constraint.
	expected_output?: string;
	rules?: string;
	// Skill hints for the runner — suggestions in the prompt, not a capability grant;
	// the node's agent profile still decides what is actually enabled.
	skill_ids?: string[];
	// The ONE work area this job binds to (T1/T2, slice 3): a project code area's
	// rel_path (".", "apps/web", …) or "ops". Absent on pre-slice-3 plans.
	target?: string | null;
	// Derived server-side from target — never authored. True means the job runs
	// against the target repo (isolated worktree when the repo flag is on).
	touches_repo?: boolean;
	// The slicer could not decide where this job works: the plan surfaces the
	// question and refuses to start until the owner picks a target.
	target_ambiguous?: boolean;
	target_question?: string | null;
	output_kind: GraphOutputKind;
	output_schema?: Record<string, unknown>;
	review_required?: boolean;
	trigger_kind?: GraphTriggerKind;
	// Script nodes only: the library script this step runs (a path inside the
	// project's scripts/ folder) plus its CLI args. First run — or any run after
	// the script's bytes changed — needs a one-time approval (hash-bound trust).
	command?: string;
	args?: string[];
	// The agent this step runs as. Null/absent = the job's own agent.
	profile_id?: number | null;
	// Canvas position. Absent until the owner drags the node, which is what lets
	// an un-dragged node stay auto-laid-out.
	x?: number;
	y?: number;
};

export type GraphEdge = { from: string; to: string };
export type WorkflowGraph = { nodes: GraphNodeDefinition[]; edges: GraphEdge[] };

export type GraphWorkflowDraft = {
	name: string;
	description?: string;
	category?: string;
	graph: WorkflowGraph;
	steps?: [];
};

export type GraphNodeStatus =
	| "pending"
	| "ready"
	| "running"
	| "review"
	| "done"
	| "failed"
	| "stale";

export type GraphNodeState = {
	id: number;
	job_id: number;
	node_id: string;
	status: GraphNodeStatus;
	output_kind: GraphOutputKind;
	inputs?: unknown;
	output?: unknown;
	checkpoint?: unknown;
	error?: string | null;
	version: number;
	run_id?: number | null;
};

export type GraphTemplate = {
	id: number;
	project_id?: number | null;
	project_slug?: string | null;
	name: string;
	description?: string;
	category?: string;
	status: string;
	graph: WorkflowGraph;
	// Declared {{inputs}}, same shape as a linear recipe's. A run fills these in and
	// the values reach each node's {{var}} through the job input.
	inputs?: WorkflowInput[];
};

// A repo job's isolated-worktree lifecycle (slice 2), attached to job payloads
// only when a worktree row exists — flag-off installs never see it.
export type JobWorktree = {
	area_id: number | null;
	branch: string;
	base_branch: string;
	base_commit: string;
	status: "active" | "merging" | "merged" | "conflict" | "discarded";
	merge_commit: string | null;
	error: string | null;
	worktree_path: string;
};

export type GraphJob = {
	id: number;
	project_id?: number | null;
	project_slug?: string | null;
	workflow_id?: number | null;
	session_id: number;
	title: string;
	status: JobStatus;
	input?: Record<string, unknown>;
	engine: "graph";
	graph: WorkflowGraph;
	node_states: GraphNodeState[];
	worktree?: JobWorktree;
	// The owner's one-line why from the reject-at-review action (slice 4);
	// set only when status became 'failed' through a rejection.
	rejected_reason?: string | null;
	created_at?: string;
	updated_at?: string;
};
// A cron schedule that fires a workflow on a cadence. `cron` is a standard
// 5-field expression (min hour day-of-month month day-of-week).
export type Schedule = {
	id: number;
	workflow_id: number;
	project_id: number | null;
	cron: string;
	input: any;
	overlap_policy: "skip" | "allow";
	enabled: boolean;
	last_run_minute: string | null;
	last_tick_at: string | null;
	created_by: number | null;
	created_at: string;
	updated_at: string;
};
export type JobStatus =
	| "queued"
	| "running"
	| "review"
	| "done"
	| "failed"
	| "cancelled";
// One step's live execution state inside a job (Step fields + run state).
export type JobStep = WorkflowStep & {
	status: "queued" | "running" | "done" | "failed" | "skipped";
	run_id: number | null;
	output_summary: string | null;
	started_at: string | null;
	finished_at: string | null;
	error: string | null;
	produced_designs?: { id: string; title: string }[];
	produced_artifacts?: import("./api/files").Artifact[];
};
export type Job = {
	id: number;
	project_id: number | null;
	project_slug?: string | null;
	workflow_id: number | null;
	session_id: number;
	title: string;
	status: JobStatus;
	// 'linear' (classic steps) or 'graph' — decides which surface can act on the job,
	// e.g. a review opens on the canvas rather than in TaskWorkspace.
	engine?: string;
	current_step_idx: number;
	input: any;
	steps_state: JobStep[];
	worktree?: JobWorktree;
	// The owner's one-line why from the reject-at-review action (slice 4).
	rejected_reason?: string | null;
	schedule_id: number | null;
	created_by: number | null;
	created_at: string;
	updated_at: string;
	started_at: string | null;
	finished_at: string | null;
	archived_at: string | null;
};
export type View =
	| "home"
	| "chat"
	| "projects"
	| "wiki"
	| "artifacts"
	| "workflows"
	| "activity"
	| "task"
	| "graph"
	| "design"
	| "profiles"
	| "runners"
	| "settings";
export type FileEntry = { name: string; type: "dir" | "file"; size: number };
