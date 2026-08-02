import React from "react";
import { IconSparkle } from "../shell/icons";
import { CompactTeachingEmpty } from "../ui/CompactTeachingEmpty";

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
	"Open deliverables with the same in-app viewer as Files",
];

const STEPS: React.ReactNode[] = [
	<>
		Write a message and press <strong>Send</strong>
	</>,
	<>Watch progress under Tasks when work is durable</>,
	<>Find outputs in Files → Deliverables or open them from the thread</>,
];

/**
 * Compact empty Chat surface: title + one lead, short tooltip hints, and a
 * modest "How it works" dialog for the fuller teaching copy. Composer below
 * stays the primary CTA.
 */
export function ChatEmpty() {
	return (
		<CompactTeachingEmpty
			className="chat-empty"
			testId="chat-empty"
			title="Start a conversation"
			lead={LEAD}
			hints={HINTS}
			helpTitle="How Chat works"
			helpLead={
				<>
					Chat is hands-on work with one agent in the active project. Type{" "}
					<code>/</code> for commands, attach files, or @-mention paths.
				</>
			}
			capabilities={CAPABILITIES}
			steps={STEPS}
			helpBtnTitle="Commands, attach, @-mentions, live tools, review, and the Send → Tasks → Files path"
			mark={<IconSparkle size={30} />}
			markTitle="Chat is the hands-on path with one agent"
		/>
	);
}
