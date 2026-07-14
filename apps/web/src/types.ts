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
	video: boolean;
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
		| "video"
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

export type GraphNodeDefinition = {
	id: string;
	name: string;
	instruction: string;
	output_kind: GraphOutputKind;
	output_schema?: Record<string, unknown>;
	review_required?: boolean;
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
	current_step_idx: number;
	input: any;
	steps_state: JobStep[];
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
	| "graph"
	| "terminal"
	| "design"
	| "video"
	| "linc-projects"
	| "profiles"
	| "runners"
	| "settings";
export type FileEntry = { name: string; type: "dir" | "file"; size: number };
