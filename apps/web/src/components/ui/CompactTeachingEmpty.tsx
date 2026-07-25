import React from "react";
import { IconClose } from "../shell/icons";

export type CompactTeachingHint = { label: string; hint: string };

export type CompactTeachingEmptyProps = {
	/** Short surface title (e.g. "Start a conversation"). */
	title: string;
	/** One short lead line; also used as the lead `title` tooltip when long enough. */
	lead: string;
	/** Optional small chips; full text lives in `hint` (title + aria-label). */
	hints?: CompactTeachingHint[];
	/** Dialog title (e.g. "How Chat works"). */
	helpTitle: string;
	/** Fuller lead inside the help dialog. */
	helpLead: React.ReactNode;
	/** Capability bullets shown only in the dialog. */
	capabilities: string[];
	/** Numbered steps shown only in the dialog. */
	steps: React.ReactNode[];
	/** Tooltip on the "How it works" trigger. */
	helpBtnTitle?: string;
	/** Optional mark / icon above the title. */
	mark?: React.ReactNode;
	/** Tooltip on the mark. */
	markTitle?: string;
	/** Heading element for the title. Default h3. */
	titleAs?: "h1" | "h2" | "h3";
	/** Extra class on the root. */
	className?: string;
	/** data-testid on the root. Default compact-teaching-empty. */
	testId?: string;
	/** Content under the help trigger (examples, primary form, …). */
	children?: React.ReactNode;
};

/**
 * Sparse empty / start surface: title + one lead, optional tooltip chips, and a
 * dismissible "How it works" dialog for the fuller teaching copy. Keeps the
 * real primary CTA (composer, Generate, Delegate) uncontested.
 */
export function CompactTeachingEmpty({
	title,
	lead,
	hints,
	helpTitle,
	helpLead,
	capabilities,
	steps,
	helpBtnTitle,
	mark,
	markTitle,
	titleAs = "h3",
	className,
	testId = "compact-teaching-empty",
	children,
}: CompactTeachingEmptyProps) {
	const [helpOpen, setHelpOpen] = React.useState(false);
	const wasOpenRef = React.useRef(false);
	const triggerRef = React.useRef<HTMLButtonElement>(null);
	const closeBtnRef = React.useRef<HTMLButtonElement>(null);
	const titleId = React.useId();

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

	const TitleTag = titleAs;

	return (
		<div
			className={["compact-empty", className].filter(Boolean).join(" ")}
			data-testid={testId}
		>
			{mark ? (
				<div className="compact-empty-mark" aria-hidden="true" title={markTitle}>
					{mark}
				</div>
			) : null}
			<TitleTag className="compact-empty-title">{title}</TitleTag>
			<p className="compact-empty-lead" title={lead}>
				{lead}
			</p>

			{hints && hints.length > 0 ? (
				<div className="compact-empty-hints" aria-label="Quick tips">
					{hints.map((h) => (
						<span
							key={h.label}
							className="compact-empty-hint"
							tabIndex={0}
							title={h.hint}
							aria-label={h.hint}
						>
							<code>{h.label}</code>
						</span>
					))}
				</div>
			) : null}

			<button
				ref={triggerRef}
				type="button"
				className="ghost-button sm compact-empty-help-btn"
				onClick={() => setHelpOpen(true)}
				aria-haspopup="dialog"
				aria-expanded={helpOpen}
				title={helpBtnTitle}
			>
				How it works
			</button>

			{children}

			{helpOpen && (
				<div
					className="modal-scrim compact-empty-help-scrim"
					onClick={closeHelp}
					data-testid={`${testId}-help-scrim`}
				>
					<div
						className="modal-card confirm-card compact-empty-help"
						role="dialog"
						aria-modal="true"
						aria-labelledby={titleId}
						tabIndex={-1}
						onClick={(e) => e.stopPropagation()}
					>
						<div className="compact-empty-help-head">
							<h3 id={titleId}>{helpTitle}</h3>
							<button
								ref={closeBtnRef}
								type="button"
								className="icon-btn compact-empty-help-close"
								onClick={closeHelp}
								aria-label="Close"
							>
								<IconClose size={16} />
							</button>
						</div>
						<p className="confirm-msg compact-empty-help-lead">{helpLead}</p>
						<ul className="teaching-empty-caps" aria-label="What you can do here">
							{capabilities.map((item) => (
								<li key={item}>{item}</li>
							))}
						</ul>
						<ol className="teaching-empty-steps" aria-label="Getting started">
							{steps.map((step, index) => (
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
