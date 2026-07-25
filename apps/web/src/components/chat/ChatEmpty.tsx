import React from "react";
import { IconClose, IconSparkle } from "../shell/icons";

const LEAD =
	"Hands-on work with one agent in this project. Type below to begin.";

const HINTS: { label: string; hint: string }[] = [
	{ label: "/", hint: "Type / for slash commands (e.g. /masterplan)" },
	{ label: "attach", hint: "Attach files from the composer paperclip" },
	{ label: "@", hint: "@-mention project files and deliverables" },
];

const CAPABILITIES = [
	"Send prompts and watch tools run live",
	"Review file changes and restore a turn when needed",
	"Open deliverables with the same in-app viewer as Archive",
];

const STEPS = [
	<>
		Write a message and press <strong>Send</strong>
	</>,
	<>Watch progress under Tasks when work is durable</>,
	<>Find outputs in Archive or open them from the thread</>,
];

/**
 * Compact empty Chat surface: title + one lead, short tooltip hints, and a
 * modest "How it works" dialog for the fuller teaching copy. Composer below
 * stays the primary CTA.
 */
export function ChatEmpty() {
	const [helpOpen, setHelpOpen] = React.useState(false);
	const wasOpenRef = React.useRef(false);
	const triggerRef = React.useRef<HTMLButtonElement>(null);
	const closeBtnRef = React.useRef<HTMLButtonElement>(null);

	const closeHelp = React.useCallback(() => setHelpOpen(false), []);

	// Focus close control on open; restore trigger focus after close.
	React.useEffect(() => {
		if (helpOpen) {
			wasOpenRef.current = true;
			closeBtnRef.current?.focus();
			const onKey = (e: KeyboardEvent) => {
				if (e.key === "Escape") {
					e.preventDefault();
					e.stopPropagation();
					closeHelp();
				}
			};
			document.addEventListener("keydown", onKey, true);
			return () => document.removeEventListener("keydown", onKey, true);
		}
		if (wasOpenRef.current) {
			wasOpenRef.current = false;
			triggerRef.current?.focus();
		}
	}, [helpOpen, closeHelp]);

	return (
		<div className="chat-empty" data-testid="chat-empty">
			<div
				className="chat-empty-mark"
				aria-hidden="true"
				title="Chat is the hands-on path with one agent"
			>
				<IconSparkle size={30} />
			</div>
			<h3>Start a conversation</h3>
			<p className="chat-empty-lead" title={LEAD}>
				{LEAD}
			</p>

			<div className="chat-empty-hints" aria-label="Quick tips">
				{HINTS.map((h) => (
					<span
						key={h.label}
						className="chat-empty-hint"
						tabIndex={0}
						title={h.hint}
						aria-label={h.hint}
					>
						<code>{h.label}</code>
					</span>
				))}
			</div>

			<button
				ref={triggerRef}
				type="button"
				className="ghost-button sm chat-empty-help-btn"
				onClick={() => setHelpOpen(true)}
				aria-haspopup="dialog"
				aria-expanded={helpOpen}
				title="Commands, attach, @-mentions, live tools, review, and the Send → Tasks → Archive path"
			>
				How it works
			</button>

			{helpOpen && (
				<div
					className="modal-scrim chat-empty-help-scrim"
					onClick={closeHelp}
					data-testid="chat-empty-help-scrim"
				>
					<div
						className="modal-card confirm-card chat-empty-help"
						role="dialog"
						aria-modal="true"
						aria-labelledby="chat-empty-help-title"
						tabIndex={-1}
						onClick={(e) => e.stopPropagation()}
					>
						<div className="chat-empty-help-head">
							<h3 id="chat-empty-help-title">How Chat works</h3>
							<button
								ref={closeBtnRef}
								type="button"
								className="icon-btn chat-empty-help-close"
								onClick={closeHelp}
								aria-label="Close"
							>
								<IconClose size={16} />
							</button>
						</div>
						<p className="confirm-msg chat-empty-help-lead">
							Chat is hands-on work with one agent in the active project. Type{" "}
							<code>/</code> for commands, attach files, or @-mention paths.
						</p>
						<ul className="teaching-empty-caps" aria-label="What you can do here">
							{CAPABILITIES.map((item) => (
								<li key={item}>{item}</li>
							))}
						</ul>
						<ol className="teaching-empty-steps" aria-label="Getting started">
							{STEPS.map((step, index) => (
								<li key={index}>
									<span className="teaching-empty-step-n" aria-hidden="true">
										{index + 1}
									</span>
									<span>{step}</span>
								</li>
							))}
						</ol>
						<div className="confirm-actions">
							<button type="button" className="primary-button" onClick={closeHelp}>
								Got it
							</button>
						</div>
					</div>
				</div>
			)}
		</div>
	);
}
