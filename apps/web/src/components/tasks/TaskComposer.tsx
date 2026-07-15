import React from "react";
import { Composer } from "../chat/Composer";
import { Dropdown } from "../ui/Dropdown";
import { IconChevronRight, IconFolder } from "../shell/icons";
import type { AppFeatures, Profile, Project } from "../../types";

export type OpsExecutionPolicy = "guarded" | "autonomous";
export type OpsTaskRequest = {
	brief: string;
	projectSlug: string;
	profileId: number | null;
	executionPolicy: OpsExecutionPolicy;
};

const cleanName = (name: string) =>
	name.replace(/\s*\((personal|private)\)\s*$/i, "");

function ProjectPicker({
	projects,
	activeProject,
	onChange,
	onManage,
}: {
	projects: Project[];
	activeProject: Project | null;
	onChange: (project: Project | null) => void;
	onManage: () => void;
}) {
	const [open, setOpen] = React.useState(false);
	const [query, setQuery] = React.useState("");
	const rootRef = React.useRef<HTMLDivElement>(null);
	React.useEffect(() => {
		if (!open) return;
		const dismiss = (event: MouseEvent) => {
			if (rootRef.current && !rootRef.current.contains(event.target as Node))
				setOpen(false);
		};
		const escape = (event: KeyboardEvent) => {
			if (event.key === "Escape") setOpen(false);
		};
		document.addEventListener("mousedown", dismiss);
		document.addEventListener("keydown", escape);
		return () => {
			document.removeEventListener("mousedown", dismiss);
			document.removeEventListener("keydown", escape);
		};
	}, [open]);
	const filtered = projects.filter((project) =>
		`${project.name} ${project.slug} ${project.path || ""}`
			.toLowerCase()
			.includes(query.trim().toLowerCase()),
	);

	return (
		<div className="task-project-picker" ref={rootRef}>
			<button
				type="button"
				className="task-context-trigger"
				aria-haspopup="dialog"
				aria-expanded={open}
				onClick={() => {
					setOpen((value) => !value);
					setQuery("");
				}}
			>
				<IconFolder size={15} />
				<span>
					{activeProject
						? cleanName(activeProject.name)
						: "Choose project or folder"}
				</span>
				<IconChevronRight size={13} />
			</button>
			{open && (
				<div
					className="task-project-popover"
					role="dialog"
					aria-label="Choose project"
				>
					<input
						autoFocus
						value={query}
						onChange={(event) => setQuery(event.target.value)}
						placeholder="Search projects"
						aria-label="Search projects"
					/>
					<div className="task-project-results">
						{filtered.map((project) => (
							<button
								type="button"
								key={project.slug}
								className={activeProject?.slug === project.slug ? "active" : ""}
								onClick={() => {
									onChange(project);
									setOpen(false);
								}}
							>
								<IconFolder size={15} />
								<span>
									<strong>{cleanName(project.name)}</strong>
									<small>{project.path}</small>
								</span>
							</button>
						))}
						{!filtered.length && <p className="muted">No projects match.</p>}
					</div>
					<button
						type="button"
						className="task-project-manage"
						onClick={() => {
							setOpen(false);
							onManage();
						}}
					>
						<span>＋</span> Choose a different folder
					</button>
				</div>
			)}
		</div>
	);
}

export function TaskComposer({
	token,
	features,
	projects,
	activeProject,
	activeProfile,
	profiles,
	onActiveProject,
	onActiveProfile,
	onManageProjects,
	onSubmit,
	onCreated,
}: {
	token: string;
	features: AppFeatures;
	projects: Project[];
	activeProject: Project | null;
	activeProfile: Profile | null;
	profiles: Profile[];
	onActiveProject: (project: Project | null) => void;
	onActiveProfile: (profile: Profile) => void;
	onManageProjects: () => void;
	onSubmit: (request: OpsTaskRequest) => Promise<number>;
	onCreated: (jobId: number) => void;
}) {
	const [error, setError] = React.useState("");
	const [executionPolicy, setExecutionPolicy] =
		React.useState<OpsExecutionPolicy>("guarded");
	const mountedRef = React.useRef(true);
	const actionSeq = React.useRef(0);
	React.useEffect(
		() => () => {
			mountedRef.current = false;
			actionSeq.current += 1;
		},
		[],
	);

	const submit = async (brief: string) => {
		if (!activeProject)
			throw new Error("Choose a project before starting a task.");
		const seq = ++actionSeq.current;
		setError("");
		try {
			const jobId = await onSubmit({
				brief,
				projectSlug: activeProject.slug,
				profileId: activeProfile?.id ?? null,
				executionPolicy,
			});
			if (mountedRef.current && seq === actionSeq.current) onCreated(jobId);
		} catch (reason) {
			const message = reason instanceof Error ? reason.message : String(reason);
			if (mountedRef.current && seq === actionSeq.current) setError(message);
			throw reason;
		}
	};

	const agentControl = (
		<label className="task-agent-control">
			<span className="sr-only">Agent</span>
			<Dropdown
				value={activeProfile ? String(activeProfile.id) : ""}
				onChange={(id) => {
					const profile = profiles.find((item) => item.id === Number(id));
					if (profile) onActiveProfile(profile);
				}}
				options={profiles.map((profile) => ({
					value: String(profile.id),
					label: profile.name,
				}))}
			/>
		</label>
	);

	return (
		<div className="task-composer-shell">
			<div className="task-composer">
				<Composer
					token={token}
					slug={activeProject?.slug}
					features={features}
					disabled={!activeProject || !activeProfile}
					placeholder="How can Ops help today?"
					textareaLabel="Task brief"
					promptModes={false}
					generateKinds={["image", "design"]}
					combinedActions
					footerContext={agentControl}
					submitIconOnly
					submitLabel="Start task"
					submittingLabel="Starting…"
					onSubmit={submit}
				/>
				<div className="task-context-bar">
					<ProjectPicker
						projects={projects}
						activeProject={activeProject}
						onChange={onActiveProject}
						onManage={onManageProjects}
					/>
					<label className="task-policy-control">
						<span className="sr-only">Execution policy</span>
						<Dropdown
							value={executionPolicy}
							onChange={(value) =>
								setExecutionPolicy(value as OpsExecutionPolicy)
							}
							options={[
								{ value: "guarded", label: "Guarded" },
								{ value: "autonomous", label: "Autonomous" },
							]}
						/>
					</label>
				</div>
			</div>
			{error && (
				<p className="task-composer-error" role="alert">
					{error}
				</p>
			)}
		</div>
	);
}
