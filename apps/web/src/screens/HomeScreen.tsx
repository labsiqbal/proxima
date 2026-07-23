import React from "react";
import { getDashboard, type Dashboard } from "../api/dashboard";
import {
	TaskComposer,
	type OpsTaskRequest,
} from "../components/tasks/TaskComposer";
import { IconActivity } from "../components/shell/icons";
import type { RunnerReadinessMap } from "../components/shell/runnerReadiness";
import { usePolling } from "../hooks/usePolling";
import type { AppFeatures, Profile, Project, View } from "../types";

export function HomeScreen({
	token,
	ownerName,
	features,
	projects,
	activeProject,
	activeProfile,
	profiles,
	runnerReadiness,
	onActiveProject,
	onActiveProfile,
	onCreateTask,
	onOpenJob,
	onSelectView,
}: {
	token: string;
	ownerName?: string;
	features: AppFeatures;
	projects: Project[];
	activeProject: Project | null;
	activeProfile: Profile | null;
	profiles: Profile[];
	runnerReadiness?: RunnerReadinessMap;
	onActiveProject: (project: Project | null) => void;
	onActiveProfile: (profile: Profile) => void;
	onCreateTask: (request: OpsTaskRequest) => Promise<number>;
	onOpenJob: (jobId: number, engine?: string) => void;
	onSelectView: (view: View) => void;
}) {
	const [data, setData] = React.useState<Dashboard | null>(null);
	const [loadError, setLoadError] = React.useState("");
	const loadSeq = React.useRef(0);
	const mounted = React.useRef(true);

	const load = React.useCallback(() => {
		const seq = ++loadSeq.current;
		getDashboard(token)
				.then((next) => {
					if (mounted.current && seq === loadSeq.current) {
						setData(next);
						setLoadError("");
					}
				})
				.catch(() => {
					if (mounted.current && seq === loadSeq.current)
						setLoadError("Dashboard status could not be refreshed. Existing data is still shown.");
				});
	}, [token]);
	React.useEffect(() => {
		mounted.current = true;
		return () => {
			mounted.current = false;
			loadSeq.current += 1;
		};
	}, []);
	usePolling(load, 5000, { restartKey: token });

	const reviewJobs = data?.reviewJobs || [];
	const reviewCount = data?.reviewCount ?? reviewJobs.length;
	const firstReview = reviewJobs[0];
	const greeting =
		new Date().getHours() < 12
			? "Good morning"
			: new Date().getHours() < 18
				? "Good afternoon"
				: "Good evening";

	return (
			<section className="ops-view ops-launcher">
				{loadError && <div className="error-bar">{loadError}</div>}
			<div className="ops-dots" aria-hidden="true" />
			<div className="ops-launcher-inner">
				<header className="ops-hero">
					<p className="ops-kicker">
						{greeting}
						{ownerName ? `, ${ownerName}` : ""}
					</p>
					<h1>What should Proxima take care of?</h1>
					<p>
						Delegate an outcome, then leave it running. Proxima will bring you
						back for review when your decision is needed.
					</p>
					<TaskComposer
						token={token}
						features={features}
						projects={projects}
						activeProject={activeProject}
						activeProfile={activeProfile}
						profiles={profiles}
						runnerReadiness={runnerReadiness}
						onActiveProject={onActiveProject}
						onActiveProfile={onActiveProfile}
						onManageProjects={() => onSelectView("projects")}
						onSubmit={onCreateTask}
						onCreated={onOpenJob}
					/>
					{!projects.length && (
						<button
							className="link-button ops-recovery-link"
							onClick={() => onSelectView("projects")}
						>
							Create or link a project first
						</button>
					)}
					{projects.length > 0 && !profiles.length && (
						<button
							className="link-button ops-recovery-link"
							onClick={() => onSelectView("profiles")}
						>
							Create or configure an Agent before starting a task
						</button>
					)}
				</header>

				{reviewCount > 0 && (
					<aside
						className="ops-attention-strip"
						aria-label="Tasks needing attention"
					>
						<span className="ops-attention-icon">
							<IconActivity size={16} />
						</span>
						<button
							type="button"
							className="ops-attention-main"
							aria-label={
								firstReview
									? `${reviewCount} ${reviewCount === 1 ? "task needs" : "tasks need"} your attention: ${firstReview.title}`
									: `${reviewCount} ${reviewCount === 1 ? "task needs" : "tasks need"} your attention`
							}
							onClick={() =>
								firstReview
									? onOpenJob(firstReview.id, firstReview.engine)
									: onSelectView("activity")
							}
						>
							<strong>
								{reviewCount} {reviewCount === 1 ? "task needs" : "tasks need"} your attention
							</strong>
							<small>
								{firstReview
									? firstReview.title
									: "Open Tasks to review pending work"}
							</small>
						</button>
						<button
							type="button"
							className="ops-attention-all"
							onClick={() => onSelectView("activity")}
						>
							View tasks
						</button>
					</aside>
				)}
			</div>
		</section>
	);
}
