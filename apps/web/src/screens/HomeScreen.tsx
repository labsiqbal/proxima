import React from "react";
import { getDashboard, type Dashboard } from "../api/dashboard";
import {
	TaskComposer,
	type OpsTaskRequest,
} from "../components/tasks/TaskComposer";
import { IconActivity } from "../components/shell/icons";
import type { AppFeatures, Profile, Project, View } from "../types";

export function HomeScreen({
	token,
	ownerName,
	features,
	projects,
	activeProject,
	activeProfile,
	profiles,
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
	onActiveProject: (project: Project | null) => void;
	onActiveProfile: (profile: Profile) => void;
	onCreateTask: (request: OpsTaskRequest) => Promise<number>;
	onOpenJob: (jobId: number) => void;
	onSelectView: (view: View) => void;
}) {
	const [data, setData] = React.useState<Dashboard | null>(null);
	const loadSeq = React.useRef(0);
	const mounted = React.useRef(true);

	const load = React.useCallback(() => {
		const seq = ++loadSeq.current;
		getDashboard(token)
			.then((next) => {
				if (mounted.current && seq === loadSeq.current) setData(next);
			})
			.catch(() => undefined);
	}, [token]);
	React.useEffect(() => {
		mounted.current = true;
		load();
		const timer = window.setInterval(load, 5000);
		return () => {
			mounted.current = false;
			loadSeq.current += 1;
			clearInterval(timer);
		};
	}, [load]);

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
			<div className="ops-dots" aria-hidden="true" />
			<div className="ops-launcher-inner">
				<header className="ops-hero">
					<p className="ops-kicker">
						{greeting}
						{ownerName ? `, ${ownerName}` : ""}
					</p>
					<h1>What should Ops take care of?</h1>
					<p>
						Delegate an outcome, then leave it running. Ops will bring you back
						for review when your decision is needed.
					</p>
					<TaskComposer
						token={token}
						features={features}
						projects={projects}
						activeProject={activeProject}
						activeProfile={activeProfile}
						profiles={profiles}
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
							onClick={() =>
								firstReview
									? onOpenJob(firstReview.id)
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
