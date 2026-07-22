import React from "react";
import type {
	ChatMessage,
	MessageReview,
	Profile,
	RunEvent,
} from "../../types";
import {
	askOriginalToRevise,
	createMessageReview,
	listMessageReviews,
	replaceAnswerWithReview,
	restoreOriginalAnswer,
} from "../../api/messageReviews";
import { Dropdown } from "../ui/Dropdown";
import { MessageContent } from "./MessageContent";

function mergeReview(
	list: MessageReview[],
	next: MessageReview,
): MessageReview[] {
	const found = list.some((r) => r.id === next.id);
	return found
		? list.map((r) => (r.id === next.id ? next : r))
		: [...list, next];
}

function statusLabel(r?: MessageReview | null): string {
	if (!r) return "Ready";
	if (r.status === "queued") return "Queued";
	if (r.status === "running") return "Validating…";
	if (r.status === "done") return "Done";
	if (r.status === "failed") return "Failed";
	return r.status;
}

export function MessageReviewSidecar({
	token,
	message,
	events,
	tokenSlug,
	profiles = [],
	onMessageUpdated,
}: {
	token?: string;
	message: ChatMessage;
	events: RunEvent[];
	tokenSlug?: string;
	profiles?: Profile[];
	onMessageUpdated?: (messageId: number, content: string) => void;
}) {
	const messageId = message.id;
	const [open, setOpen] = React.useState(false);
	const [reviews, setReviews] = React.useState<MessageReview[]>([]);
	const [selectedId, setSelectedId] = React.useState<number | null>(null);
	const [loading, setLoading] = React.useState(false);
	const [busy, setBusy] = React.useState(false);
	const [expanded, setExpanded] = React.useState(true);
	const [reviewerProfileId, setReviewerProfileId] = React.useState<
		number | null
	>(null);
	const [error, setError] = React.useState("");
	const [notice, setNotice] = React.useState("");
	const mountedRef = React.useRef(true);
	const seqRef = React.useRef(0);

	React.useEffect(() => {
		mountedRef.current = true;
		return () => {
			mountedRef.current = false;
			seqRef.current += 1;
		};
	}, []);

	React.useEffect(() => {
		if (!messageId) return;
		const changed = events
			.map((ev) =>
				String(ev.type).startsWith("message_review.")
					? (ev.payload as { review?: MessageReview }).review
					: null,
			)
			.filter(
				(review): review is MessageReview =>
					!!review && review.source_message_id === messageId,
			);
		if (!changed.length) return;
		setReviews((current) =>
			changed.reduce((next, review) => mergeReview(next, review), current),
		);
		const latest = changed[changed.length - 1];
		setOpen(true);
		setSelectedId(latest.id);
		setExpanded(["queued", "running"].includes(latest.status));
	}, [events, messageId]);

	const selected =
		reviews.find((r) => r.id === selectedId) ||
		reviews[reviews.length - 1] ||
		null;

	const load = async () => {
		if (!token || !messageId) return;
		const seq = ++seqRef.current;
		setLoading(true);
		setError("");
		try {
			const body = await listMessageReviews(token, messageId);
			if (!mountedRef.current || seq !== seqRef.current) return;
			setReviews(body.reviews);
			setSelectedId(body.reviews[body.reviews.length - 1]?.id || null);
		} catch (e) {
			if (mountedRef.current && seq === seqRef.current)
				setError(e instanceof Error ? e.message : String(e));
		} finally {
			if (mountedRef.current && seq === seqRef.current) setLoading(false);
		}
	};

	const toggle = () => {
		setOpen((o) => !o);
		if (!open && reviews.length === 0) void load();
	};

	const runValidate = async () => {
		if (!token || !messageId) return;
		const seq = ++seqRef.current;
		setBusy(true);
		setError("");
		setNotice("");
		try {
			const body = await createMessageReview(token, messageId, {
				reviewer_profile_id: reviewerProfileId,
			});
			if (!mountedRef.current || seq !== seqRef.current) return;
			setReviews((current) => mergeReview(current, body.review));
			setSelectedId(body.review.id);
			setOpen(true);
			setExpanded(true);
		} catch (e) {
			if (mountedRef.current && seq === seqRef.current)
				setError(e instanceof Error ? e.message : String(e));
		} finally {
			if (mountedRef.current && seq === seqRef.current) setBusy(false);
		}
	};

	const replaceAnswer = async () => {
		if (!token || !selected) return;
		const seq = ++seqRef.current;
		setBusy(true);
		setError("");
		setNotice("");
		try {
			const body = await replaceAnswerWithReview(token, selected.id);
			if (!mountedRef.current || seq !== seqRef.current) return;
			setReviews((current) => mergeReview(current, body.review));
			onMessageUpdated?.(body.message.id, body.message.content);
			setExpanded(false);
			setNotice("Answer replaced.");
		} catch (e) {
			if (mountedRef.current && seq === seqRef.current)
				setError(e instanceof Error ? e.message : String(e));
		} finally {
			if (mountedRef.current && seq === seqRef.current) setBusy(false);
		}
	};

	const restoreOriginal = async () => {
		if (!token || !selected) return;
		const seq = ++seqRef.current;
		setBusy(true);
		setError("");
		setNotice("");
		try {
			const body = await restoreOriginalAnswer(token, selected.id);
			if (!mountedRef.current || seq !== seqRef.current) return;
			setReviews((current) => mergeReview(current, body.review));
			onMessageUpdated?.(body.message.id, body.message.content);
			setNotice("Original restored.");
		} catch (e) {
			if (mountedRef.current && seq === seqRef.current)
				setError(e instanceof Error ? e.message : String(e));
		} finally {
			if (mountedRef.current && seq === seqRef.current) setBusy(false);
		}
	};

	const copyRevised = async () => {
		if (!selected?.revised_content) return;
		setError("");
		try {
			await navigator.clipboard.writeText(selected.revised_content);
			setNotice("Revised text copied.");
		} catch (e) {
			setError(e instanceof Error ? e.message : String(e));
		}
	};

	const askOriginal = async () => {
		if (!token || !selected) return;
		const seq = ++seqRef.current;
		setBusy(true);
		setError("");
		setNotice("");
		try {
			const body = await askOriginalToRevise(token, selected.id);
			if (!mountedRef.current || seq !== seqRef.current) return;
			setReviews((current) => mergeReview(current, body.review));
			setSelectedId(body.review.id);
			setExpanded(true);
		} catch (e) {
			if (mountedRef.current && seq === seqRef.current)
				setError(e instanceof Error ? e.message : String(e));
		} finally {
			if (mountedRef.current && seq === seqRef.current) setBusy(false);
		}
	};

	const reviewerLabel = selected?.reviewer_profiles?.length
		? selected.reviewer_profiles.map((p) => p.name).join(", ")
		: "Auto reviewer";
	const reviewerValue = reviewerProfileId == null ? "" : String(reviewerProfileId);
	const reviewerOptions = [
		{ value: "", label: "Auto-pick", badge: "Different agent" },
		...profiles.map((p) => ({
			value: String(p.id),
			label: p.name,
			badge: p.runner_id || "agent",
		})),
	];
	const onReviewerChange = (value: string) => {
		setReviewerProfileId(value ? Number(value) : null);
	};
	// Status lives in the status pill only — the summary carries what the pill
	// doesn't (who reviewed, verdict, extras), so nothing reads twice.
	const summary = selected
		? [
				reviewerLabel,
				selected.status === "done" ? selected.verdict || "unclear" : null,
				selected.gaps?.length ? `${selected.gaps.length} gaps` : null,
				selected.revised_content ? "revised available" : null,
				selected.applied_at ? "applied" : null,
			]
				.filter(Boolean)
				.join(" · ")
		: "Pick a reviewer for a sidecar review";
	let replaceLabel = "Replace answer";
	if (busy) replaceLabel = "Applying…";
	else if (selected?.applied_at) replaceLabel = "Replace again";

	return (
		<div className={`message-review-wrap ${open ? "open" : ""}`}>
			<div className="message-actions">
				<button
					type="button"
					className="message-action"
					onClick={toggle}
					disabled={!token || !messageId}
				>
					Validate
				</button>
			</div>
			{open && (
				<div
					className={`message-review-sidecar ${expanded ? "expanded" : "collapsed"}`}
				>
					<div className="review-head">
						<button
							type="button"
							className="review-title"
							onClick={() => setExpanded((v) => !v)}
						>
							<strong>Validate</strong>
							<small>{summary}</small>
						</button>
						<div className="review-head-actions">
							<span className={`review-status ${selected?.status || "ready"}`}>
								{statusLabel(selected)}
							</span>
							{selected && (
								<button
									type="button"
									className="message-action"
									onClick={() => setExpanded((v) => !v)}
								>
									{expanded ? "Minimize" : "Expand"}
								</button>
							)}
						</div>
					</div>
					{reviews.length > 1 && (
						<div className="review-tabs">
							{reviews.map((r, index) => (
								<button
									type="button"
									key={r.id}
									className={r.id === selected?.id ? "active" : ""}
									onClick={() => setSelectedId(r.id)}
								>
									{/* Ordinal per message (run 1, 2, …), not the DB row id. */}
									#{index + 1} · {r.verdict || r.status}
								</button>
							))}
						</div>
					)}
					{!selected && (
						<div className="review-empty">
							<p>Validation stays here and won’t change chat until applied.</p>
							<div className="review-picker">
								<label>
									<span>Reviewer</span>
									<Dropdown
										className="review-dd"
										value={reviewerValue}
										options={reviewerOptions}
										onChange={onReviewerChange}
										disabled={busy || loading}
										minWidth={220}
										dropUp
									/>
								</label>
							</div>
							<button
								type="button"
								className="review-btn review-btn-accent"
								disabled={busy || loading || !token}
								onClick={() => void runValidate()}
							>
								{busy ? "Starting…" : "Run validation"}
							</button>
						</div>
					)}
					{selected && (
						<div className="review-body">
							{/* Collapsed: the head line already shows the summary — no repeat. */}
							{expanded &&
								(selected.status === "queued" ||
									selected.status === "running") && (
									<div className="review-live">
										<span className="shimmer">
											{selected.status === "queued" ? "Queued…" : "Validating…"}
										</span>
									</div>
								)}
							{expanded && selected.status === "failed" && (
								<div className="error-text">
									{selected.error || "Review failed."}
								</div>
							)}
							{expanded && selected.status === "done" && (
								<>
									<div className="review-section">
										<span>Verdict</span>
										<strong>{selected.verdict || "unclear"}</strong>
									</div>
									{selected.gaps?.length > 0 && (
										<div className="review-section">
											<span>Gaps / risks</span>
											<ul>
												{selected.gaps.map((g, i) => (
													<li key={i}>{g}</li>
												))}
											</ul>
										</div>
									)}
									{selected.depends_on_input?.length > 0 && (
										<div className="review-section">
											<span>Depends on unanswered input</span>
											<ul>
												{selected.depends_on_input.map((g, i) => (
													<li key={i}>{g}</li>
												))}
											</ul>
										</div>
									)}
									{selected.revised_content && (
										<div className="review-section">
											<span>Revised version</span>
											<MessageContent
												content={selected.revised_content}
												token={token}
												slug={tokenSlug}
											/>
										</div>
									)}
									{selected.suggested_next_move && (
										<div className="review-section">
											<span>Suggested next move</span>
											<p>{selected.suggested_next_move}</p>
										</div>
									)}
								</>
							)}
							{selected.status === "done" && (
								<div className="review-actions">
									{selected.revised_content && (
										<button
											type="button"
											className="review-btn review-btn-accent"
											disabled={busy}
											onClick={() => void replaceAnswer()}
										>
											{replaceLabel}
										</button>
									)}
									{selected.applied_at && selected.source_original_content && (
										<button
											type="button"
											className="review-btn"
											disabled={busy}
											onClick={() => void restoreOriginal()}
										>
											Restore original
										</button>
									)}
									{selected.revised_content && (
										<button
											type="button"
											className="review-btn"
											disabled={busy}
											onClick={() => void copyRevised()}
										>
											Copy revised
										</button>
									)}
									<button
										type="button"
										className="review-btn"
										disabled={busy}
										onClick={() => void askOriginal()}
									>
										{busy ? "Starting…" : "Ask source to merge"}
									</button>
								</div>
							)}
						</div>
					)}
					{selected && ["done", "failed"].includes(selected.status) && (
						<div className="review-rerun">
							<label>
								<span>Reviewer</span>
								<Dropdown
									className="review-dd"
									value={reviewerValue}
									options={reviewerOptions}
									onChange={onReviewerChange}
									disabled={busy}
									minWidth={220}
									dropUp
								/>
							</label>
							<button
								type="button"
								className="review-btn review-btn-accent"
								disabled={busy || !token}
								onClick={() => void runValidate()}
							>
								{busy ? "Starting…" : "Run validation"}
							</button>
						</div>
					)}
					{notice && <div className="review-notice">{notice}</div>}
					{error && <div className="error-text">{error}</div>}
				</div>
			)}
		</div>
	);
}
