# Proxima Alpha build status

**Masterplan:** v1.1, Gate C approved

**Completed:** 2026-07-24
**Branch:** `fm/proxima-alpha-build`

Milestones are checked only against the evidence below.

- [x] **M1 - Alpha session + desk shell**
  - `sessions.mode='alpha'` and hidden `profiles.system_kind='alpha'`; normal profile/session APIs exclude both.
  - Alpha is a first-class sidebar destination with loading, empty, error/retry, populated, and responsive desk states.
  - Evidence: `tests/test_alpha.py::test_alpha_desk_creates_hidden_system_identity`; `AlphaScreen.test.tsx`; live browser snapshot showed Alpha nav/desk and selectable backing runner.
- [x] **M2 - In-process tools + one job**
  - Server parses Alpha's structured calls and invokes allowlisted Python handlers directly. No loopback HTTP/curl control plane.
  - Dispatch creates Autonomous jobs and `alpha.job.create` audits; structured errors return to the Alpha thread.
  - Evidence: live Alpha turn used `dispatch_jobs`, returned job ids 1/2, and both real Claude-backed workers completed files; `test_disallowed_alpha_tool_returns_structured_error`.
- [x] **M3 - Multi-dispatch + capacity**
  - Alpha max parallel is 3 at the worker claim seam; excess runs stay queued and the desk shows running/free/queued separately.
  - Evidence: `test_alpha_in_process_multi_dispatch_is_autonomous_checkpointed_and_scoped_to_three`; `test_alpha_capacity_counts_each_queued_worker_run`; live E2E dispatched two workers concurrently and both completed.
- [x] **M4 - Scoped permission behavior**
  - Alpha sessions and children auto-approve ACP prompts by durable scope. Ordinary jobs keep owner permission behavior and materialize hidden-session asks in Attention.
  - Evidence: live child runs emitted `approval.auto` for Write and Terminal tools; `test_permission_attention_closes_when_choice_is_delivered`; Alpha run/child scope assertions in `test_alpha.py`.
- [x] **M5 - Job-scoped checkpoints**
  - Pre-start job/node/run JSON plus git/worktree refs only; FIFO 30 unpinned; impact preview, confirmation, dirty/conflicting-work refusal, pin and restore routes. Main-checkout SHAs are reference-only; only a job-owned worktree may be reset.
  - Evidence: `test_checkpoint_fifo_keeps_thirty_unpinned`; `test_checkpoint_restore_never_resets_the_shared_project_checkout`; `test_alpha_repo_checkpoint_captures_and_restores_the_job_worktree`; live desk showed two checkpoints and a restore impact dialog listing only job state and refs.
- [x] **M6 - Global Attention hybrid**
  - Shell badge unifies job review/diff, satpam restart, hash-visible script trust, job permission, and Alpha decision/budget items. Only `inline_ok` rows expose actions; every row deep-links.
  - Evidence: `AttentionInbox.test.tsx`; `test_script_trust_attention_shows_hash_and_uses_in_process_approval`; live browser showed an Alpha decision badge/popover and deep-link.
- [x] **M7 - Turn restore in Chat**
  - ACP tool events trigger bounded path journals; preview lists paths; session cascade retains them only for the session; active Alpha work warns before confirmation.
  - Evidence: `test_turn_restore_previews_paths_and_restores_pre_turn_content`; live normal Chat run created `artifacts/chat-restore.txt`, showed `Restore 1 changed path`, previewed the path, restored it, and verified the file was removed.
- [x] **M8 - Unattended supervisor + budgets**
  - Desk toggle, Settings turn/wall/optional-token values, and a sibling supervisor that starts only queued Alpha work. Exhaustion disables mode and creates Attention. Satpam alone owns stuck recovery.
  - Evidence: `test_unattended_supervisor_enforces_turn_budget_and_surfaces_clean_stop`; live Settings rendered validated numeric budget fields. Current ACP events expose no token usage, so turn and wall-clock are the enforced caps and docs state this explicitly.
- [x] **M9 - Core tour + Help chapters**
  - Show-once four-step core tour after setup; server completion state; replay and feature-aware full product-map chapters under Settings -> Help & Tours.
  - Evidence: `CoreTour.test.tsx`; browser drove all four steps and Help chapters. Keyboard E2E found an initial focus escape, then verified the fix traps Tab and wraps inside the modal.
- [x] **M10 - Full QA and interaction baseline**
  - API: Ruff clean and `702 passed` (4 upstream deprecation warnings).
  - Web: `51` files / `262 passed`; TypeScript + Vite production build passed (existing large-chunk advisory only).
  - Browser: actual owner setup -> core tour -> Alpha -> two-worker dispatch -> checkpoints -> Attention -> Settings/Help -> Chat turn restore. Alpha loading, empty, populated, in-flight/disabled, network error, retry success, desktop, and 360px mobile states were exercised. Mobile measured `scrollWidth=360` at `innerWidth=360`. Tour and primary flow were keyboard-driven; reduced motion and token-based styles are implemented in `styles.css`.

## Authority and safety notes

- P1 Alpha product tools are an in-process allowlist.
- P2 Alpha-spawned jobs default to Autonomous; product review gates remain separate.
- P3 ACP auto-approve is scoped to the Alpha session and Alpha-spawned jobs, never enabled globally.
- Job work may use the existing BYO git/gh commit, push, and PR path.
- Unattended has no destructive admin handler. Alpha never restarts stuck work; satpam remains the sole stuck-run authority.
