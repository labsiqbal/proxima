# Proxima Core Flows

> **How to read this doc:** these are the *intended* flows guiding implementation —
> not all of them exist yet. Sections marked *(planned)* (**Main Project**,
> **Workflow versioning**, **Integrations**, **artifact/app Publish**, per-agent
> `AGENT.md` memory) are design targets with **no code behind them today**. Design
> Studio is an active, feature-flagged surface; image generation is active; Video
> Studio was removed.

Updated: 2026-06-27

This document turns the product vision into concrete user flows and ownership rules. It should guide implementation decisions before adding or changing product surfaces.

## Core Objects

Proxima should revolve around these primitives:

- Project: the ownership boundary for work.
- Main Project: pinned global/abstract workspace. *(planned)*
- Agent: global profile/persona with its own memory.
- Session: project-scoped chat context.
- Run: one execution by an agent.
- Artifact: concrete output or previewable result.
- Workflow: reusable process definition.
- Workflow Version: draft/active/history of a workflow. *(planned)*
- Activity Job: tracked execution history/status.
- Wiki Note: project memory.

## First-Time Onboarding

First-time user flow:

```text
Onboarding
→ create new project or connect existing folder
→ setup/select agent
→ enter Chat in the default project
```

### Blank Project

For a new blank project, Proxima creates a minimal scaffold:

```text
README.md
AGENTS.md
wiki/index.md
artifacts/
```

If the user uploads documents during onboarding, add:

```text
context/
```

The user lands in a blank chat. No forced wizard after setup.

### Connected Folder

For an existing folder:

```text
connect folder
→ preserve folder structure
→ agent scans/summarizes the project
→ create/update wiki/index.md
→ optionally create more wiki notes based on folder size/structure
→ enter Chat in that project
→ show a short message and result card to open wiki/index.md
```

The agent has full read capacity in the active project. The summarizer should be intelligent about noise and generated/vendor/binary files, but this is an indexing-quality decision, not a permission restriction.

## Returning User Flow

Returning users enter Home.

Home is global across projects and should prioritize:

1. Continue: last chat, last project, last opened output/surface.
2. Activity: running, failed, review-needed, or queued work across projects.
3. Recent outputs: recent artifacts/designs/files/wiki outputs across projects.

Home is not project-scoped. All other primary surfaces are project-scoped.

## Global Project Context

The active project is global app context for these surfaces:

- Chat
- Design
- Workflows
- Activity
- Artifacts
- Files
- Wiki

Home is the only primary exception.

Switching active project changes the context for those surfaces.

Rules:

- active project owns all writes,
- cross-project writes do not happen unless the user explicitly asks for copy/move,
- implicit cross-project context is not allowed,
- cross-project read-only access is allowed only when explicitly requested,
- cross-project read-only targets are files and artifacts.

## Main Project *(planned — not yet in code)*

Main Project is pinned above regular projects.

Use Main Project for:

- global brainstorming,
- personal/global memory,
- reusable ideas,
- abstract product thinking,
- global/plugin-like workflows.

Main Project is still a project. It uses the same project primitives:

- chat,
- wiki,
- artifacts,
- workflows,
- files.

## Chat Flow

Every chat is project-scoped.

Default behavior:

```text
if user request is clear:
  execute
else:
  ask clarifying question
```

Question mechanisms:

- natural language,
- interactive qform,
- approval/confirmation card when needed.

Chat is a gateway. It can create or route to structured surfaces:

- Design,
- Wiki,
- Files,
- Workflows,
- Artifacts,
- Activity.

### Tool Cards

Tool cards are process receipts.

During a run:

- show compact live tool cards,
- show status and short title,
- do not expose full raw payload by default.

After a reply:

- persisted Agent Activity may be collapsed/expanded.

Full payload belongs in debug traces or structured Activity, not normal chat.

### Result Cards

When work creates concrete outputs, chat should show structured result cards.

Result card routing:

```text
Design → Design Studio editor
Image → Artifacts image viewer/detail
Document/File → Files, unless it is wiki-owned
wiki/*.md → Wiki
App → Artifacts app preview
Workflow created → Workflow Editor
Agent memory update → Settings > Agents > selected agent > Memory
Project instruction update → Project settings / AGENTS.md
```

Result cards must preserve origin:

```text
Chat result card
→ target surface
→ Back to chat
→ original chat session
```

This fixes the current broken loop where users must manually open Design, find a generated design, and back returns to the gallery instead of the originating chat.

## Memory Flow

### Agent Memory *(planned — not yet in code)*

Each global agent/profile has memory/instructions conceptually stored as:

```text
AGENT.md
```

Agent memory captures:

- durable user preferences for that agent,
- role behavior,
- working style,
- repeated output preferences,
- cross-project agent habits.

Agent memory may auto-update and can be edited manually.

Manual editing location:

```text
Settings
→ Agents
→ select agent
→ Memory / AGENT.md
```

If an agent updates memory during chat, show a visible result/event card.

### Project Instructions

Each project has:

```text
AGENTS.md
```

Project instructions capture:

- project rules,
- conventions,
- build/test commands,
- brand/style constraints,
- folder conventions,
- durable project-specific preferences.

Manual editing location:

```text
Project settings → Instructions
or Files → AGENTS.md
```

### Project Wiki

Each project wiki has:

```text
wiki/index.md
```

`wiki/index.md` is the living index and summary of the project wiki.

Any wiki note create/update should also update `wiki/index.md`.

Suggested structure:

```md
# Project Wiki Index

## Summary

## Key Notes
- [[note]] — one-line summary

## Decisions
- YYYY-MM-DD — decision summary → [[note]]

## Open Questions

## Recent Updates
- YYYY-MM-DD — what changed → [[note]]
```

Context retrieval is hybrid:

- always include lightweight `wiki/index.md`,
- search/read detailed wiki notes only when needed.

## Design Flow

Design Studio is the editor/creation surface.

Design outputs should:

- be editable,
- keep text as text,
- use layers/groups/artboards,
- appear in Design Studio "Your designs",
- appear in Artifacts as design outputs,
- be directly openable from chat result cards.

Design result card should open Design Studio directly, not Artifacts.

## Artifacts Flow

Artifacts is the current project's universal output gallery.

Filters:

```text
All
Design
Image
Document
App
Data
Other
```

Artifacts may index outputs from:

- `artifacts/`,
- Design Studio scenes,
- generated files,
- app folders,
- exported PDFs/images,
- workflow outputs,
- agent-registered outputs.

`artifacts/` is a default dropzone, not the only valid location.

### App Artifacts

Run App / app preview should live in Artifacts.

Auto-detect app candidates by scanning for `package.json` and framework hints:

- Astro: `astro` dependency or `astro` scripts,
- Vite: `vite`,
- Next: `next`,
- Remix,
- SvelteKit,
- Nuxt,
- other package/script hints.

If many apps are detected:

- list all app candidates,
- let user hide/exclude folders from Artifacts,
- let user set primary app,
- let user edit run command.

Exclude controls live in Artifacts card menus.

Files may still preview individual HTML/Markdown files while editing.

## Files Flow

Files is raw filesystem access for the active project.

Use Files for:

- browse tree,
- edit files,
- save files,
- single-file HTML/Markdown preview,
- inspect generated output when needed.

Do not use Files as the primary app/output consumption surface. That belongs in Artifacts.

## Workflow Flow

Workflow lifecycle:

```text
draft
→ iterate/test
→ publish active version
→ run/schedule
→ version/restore as needed
```

Workflow created from chat starts as draft and opens Workflow Editor.

### Workflow Iterate

Workflow Iterate is the lab for improving workflow definitions.

It should let users inspect and edit:

- each step prompt/instruction,
- expected output,
- step result,
- consistency,
- full recipe,
- test runs.

It edits the workflow definition, not one completed run's output.

### Workflow Versioning *(planned — not yet in code)*

Rules:

- one active version per workflow,
- editing an active workflow creates or works on a draft version,
- active version remains recoverable,
- user can publish, discard, or restore,
- manual run can choose version,
- schedule can choose version,
- schedules do not silently change version unless user updates them.

Campaign/project/context differences should usually be run inputs, not separate active versions. If step logic is materially different, create a different workflow.

### Main And Project Workflows

Main Project can hold global/plugin-like workflows.

Regular project workflows are local to that project.

Project A cannot directly use Project B workflows.

Main/global workflows can be made available as reusable/plugin-like workflows, but project workflows do not automatically leak across projects.

If a project workflow becomes generally useful:

```text
Promote to Main
→ generalize project-specific assumptions
→ save as Main workflow
```

## Activity Flow

Activity is the execution/history surface for the active project.

Always creates Activity:

- `/goal`,
- workflow run,
- scheduled run.

Agent/harness may create Activity for:

- long-running work,
- multi-step output work,
- work needing review,
- multi-output production work.

Normal chat does not create Activity.

Home can show cross-project Activity summaries, but Activity screen itself is active-project scoped.

## Tasks Flow

Top-level Tasks are not a core navigation surface.

If task-like work remains internally, model it as:

```text
one-step Activity job
```

Ad-hoc work starts from Chat or `/goal`. Repeatable work starts from Workflows.

## Integration Flow *(planned — not yet in code)*

Integrations are global connections with project bindings.

Examples:

- GitHub,
- Cloudflare,
- Google services,
- MCP servers,
- external APIs.

Rules:

- connect accounts/services globally,
- bind selected connections/resources to a project,
- active project agents can only use bound integrations,
- external destructive actions require confirmation.

Project Settings is where project-specific bindings are managed.

Onboarding should not force integrations. The agent can guide users later:

- "Connect GitHub to create a repo."
- "Connect Cloudflare to publish this app."
- "Bind Google Drive to summarize these docs."

### GitHub

Target capability is full access when connected and bound:

- create repo,
- delete/archive repo with confirmation,
- clone,
- bind repo,
- branch/commit/push,
- open PR,
- read/write issues,
- inspect CI/check logs.

### Cloudflare

Target capability is full access when connected and bound:

- accounts/zones,
- DNS records,
- tunnels,
- Pages/Workers,
- routes/domains,
- publish/unpublish app or artifact.

Destructive/external-impact actions require confirmation.

### MCP

MCP servers are global connections with project bindings.

Agents are global and can be used in any project, but they only get the MCP/tools enabled for the active project.

## Terminal Flow

Terminal is a global utility, opening in the active project by default.

It is not primary navigation. It should remain available from utility/profile menus.

## Remote Access And Publish Flow *(publish is planned — not yet in code)*

Remote access:

```text
Settings → Remote Access
```

Purpose:

- owner can use Proxima from mobile/remote,
- an in-app setup guide (Tailscale; Cloudflare Tunnel + custom domain + Access) —
  Proxima documents the safe paths, it does not manage tunnels,
- keep access private/protected.

Artifact/app publishing:

```text
Artifacts → artifact/app card → Publish
```

Purpose:

- portfolio/demo/share link,
- explicit per artifact/app,
- private by default,
- publish/unpublish controlled by user.

Remote access to Proxima and public artifact publishing are separate product flows.
