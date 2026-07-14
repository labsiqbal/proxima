# API Reference

> **GENERATED FILE — do not edit by hand.** Regenerate with `python3 scripts/gen_docs.py`.


145 endpoints across 12 route modules. All paths are relative to the API base (e.g. `http://127.0.0.1:8765`). Auth: single-user — the SPA obtains a bearer token via `POST /auth/auto`, then sends it as `Authorization: Bearer <token>`.


## Modules

- [`routes/admin.py`](#routes-admin-py) — 3 endpoints
- [`routes/auth.py`](#routes-auth-py) — 9 endpoints
- [`routes/chat.py`](#routes-chat-py) — 24 endpoints
- [`routes/design.py`](#routes-design-py) — 4 endpoints
- [`routes/files.py`](#routes-files-py) — 54 endpoints
- [`routes/profiles.py`](#routes-profiles-py) — 8 endpoints
- [`routes/projects.py`](#routes-projects-py) — 7 endpoints
- [`routes/reviews.py`](#routes-reviews-py) — 6 endpoints
- [`routes/update.py`](#routes-update-py) — 3 endpoints
- [`routes/wiki.py`](#routes-wiki-py) — 8 endpoints
- [`routes/work.py`](#routes-work-py) — 16 endpoints
- [`main.py (app-level)`](#main-py-app-level) — 3 endpoints


## routes/admin.py

| Method | Path | Handler | Description |
| --- | --- | --- | --- |
| GET | `/api/audit` | `list_audit` |  |
| GET | `/api/debug/logs` | `debug_logs` |  |
| POST | `/api/debug/reap-orphaned-jobs` | `reap_orphaned_jobs` |  |


## routes/auth.py

| Method | Path | Handler | Description |
| --- | --- | --- | --- |
| GET | `/api/me` | `me` | Boot resume: authenticated by the HttpOnly cookie, echo the session token |
| GET | `/api/setup/status` | `setup_status` |  |
| POST | `/auth/auto` | `auth_auto` | Passwordless auto-login (network-only mode). Disabled once a password is |
| POST | `/auth/change-password` | `change_password` | Change the password (from Settings): verify the current one, set the new, |
| POST | `/auth/login` | `login_with_password` | Verify the owner's password and start a session (no expiry until logout). |
| POST | `/auth/logout` | `logout` |  |
| POST | `/auth/resume` | `resume` | Boot resume: authenticated by the HttpOnly cookie, echo the session token |
| POST | `/auth/set-password` | `set_password` | First-run: set the owner's password. Only allowed while none is set (later |
| GET | `/health` | `health` |  |


## routes/chat.py

| Method | Path | Handler | Description |
| --- | --- | --- | --- |
| POST | `/api/chat/send` | `chat_send` |  |
| GET | `/api/dashboard` | `dashboard` | Aggregated real-data summary for the Home dashboard. |
| GET | `/api/runs/active` | `active_runs` | Sessions with an in-flight run, so the sidebar can show a thinking |
| DELETE | `/api/runs/{run_id}` | `delete_run` |  |
| GET | `/api/runs/{run_id}` | `get_run` |  |
| POST | `/api/runs/{run_id}/cancel` | `cancel_run` |  |
| POST | `/api/runs/{run_id}/permission` | `respond_permission` | Deliver the user's interactive card choice back to the waiting agent. |
| GET | `/api/search` | `search` |  |
| GET | `/api/sessions` | `list_sessions` |  |
| POST | `/api/sessions` | `create_session` |  |
| DELETE | `/api/sessions/{session_id}` | `delete_session` |  |
| PATCH | `/api/sessions/{session_id}` | `update_session` |  |
| GET | `/api/sessions/{session_id}/events` | `list_events` |  |
| GET | `/api/sessions/{session_id}/events/stream` | `stream_events` |  |
| POST | `/api/sessions/{session_id}/goal` | `start_goal` |  |
| POST | `/api/sessions/{session_id}/goal/cancel` | `cancel_goal` |  |
| GET | `/api/sessions/{session_id}/messages` | `list_messages` |  |
| POST | `/api/sessions/{session_id}/messages` | `create_message` |  |
| POST | `/api/sessions/{session_id}/promote-workflow` | `promote_workflow` |  |
| POST | `/api/sessions/{session_id}/runs` | `create_run` |  |
| POST | `/api/sessions/{session_id}/wiki-note/commit` | `wiki_note_commit` |  |
| POST | `/api/sessions/{session_id}/wiki-note/draft` | `wiki_note_draft` |  |
| WS | `/api/ws/sessions/{session_id}` | `ws_events` |  |
| WS | `/api/ws/terminal` | `ws_terminal` | In-browser PTY shell (like SSH from the cockpit). Auth via ?token= or the |


## routes/design.py

| Method | Path | Handler | Description |
| --- | --- | --- | --- |
| POST | `/api/projects/{slug}/design/brand-guide` | `generate_brand_guide` | Kick off an agent run that synthesises a project's brand guideline into |
| POST | `/api/projects/{slug}/design/image` | `design_image` | Generate (text→image) or edit (image+prompt→image) via the configured |
| GET | `/api/projects/{slug}/design/image-models` | `design_image_models` | For the codex provider there's no static model list (login-based); for |
| POST | `/api/projects/{slug}/designs/from-image` | `design_from_image` | Seed a new Design Studio scene containing an existing project image as a |


## routes/files.py

| Method | Path | Handler | Description |
| --- | --- | --- | --- |
| API_ROUTE | `/api/appview/{slug}/{path:path}` | `app_view` |  |
| GET | `/api/events` | `hyperframes_events` |  |
| GET | `/api/preview/{slug}/{file_path:path}` | `project_preview` |  |
| POST | `/api/projects/{slug}/app/start` | `app_start` |  |
| GET | `/api/projects/{slug}/app/status` | `app_status` |  |
| POST | `/api/projects/{slug}/app/stop` | `app_stop` |  |
| GET | `/api/projects/{slug}/apps` | `detect_apps` | Scan the project for runnable apps so the user picks one instead of |
| GET | `/api/projects/{slug}/artifacts` | `list_artifacts` | Typed artifacts recently produced in a project (design/app/page/doc/file) so |
| GET | `/api/projects/{slug}/file` | `project_read_file` |  |
| PUT | `/api/projects/{slug}/file` | `project_write_file` |  |
| DELETE | `/api/projects/{slug}/fs` | `project_delete` |  |
| POST | `/api/projects/{slug}/fs/mkdir` | `project_mkdir` |  |
| POST | `/api/projects/{slug}/fs/rename` | `project_rename` |  |
| GET | `/api/projects/{slug}/raw` | `project_raw` |  |
| GET | `/api/projects/{slug}/tree` | `project_tree` |  |
| POST | `/api/projects/{slug}/upload` | `project_upload` |  |
| GET | `/api/projects/{slug}/videos` | `list_videos` |  |
| POST | `/api/projects/{slug}/videos` | `create_video` |  |
| DELETE | `/api/projects/{slug}/videos/{video_id}` | `delete_video` |  |
| POST | `/api/projects/{slug}/videos/{video_id}/import-file` | `video_import_file` | Copy an existing project media file into the video project's assets/ so the |
| POST | `/api/projects/{slug}/videos/{video_id}/lint` | `lint_video` |  |
| POST | `/api/projects/{slug}/videos/{video_id}/render` | `render_video` |  |
| POST | `/api/projects/{slug}/videos/{video_id}/studio/start` | `start_video_studio` |  |
| API_ROUTE | `/api/projects/{studio_id}/duplicate-file` | `video_studio_duplicate_file_api` |  |
| API_ROUTE | `/api/projects/{studio_id}/file-mutations/{mutation_path:path}` | `video_studio_file_mutations_api` |  |
| API_ROUTE | `/api/projects/{studio_id}/files/{file_path:path}` | `video_studio_files_api` |  |
| API_ROUTE | `/api/projects/{studio_id}/gsap-mutations/{mutation_path:path}` | `video_studio_gsap_mutations_api` |  |
| API_ROUTE | `/api/projects/{studio_id}/lint` | `video_studio_lint_api` |  |
| API_ROUTE | `/api/projects/{studio_id}/preview` | `video_studio_preview_root_api` |  |
| API_ROUTE | `/api/projects/{studio_id}/preview/{preview_path:path}` | `video_studio_preview_api` |  |
| API_ROUTE | `/api/projects/{studio_id}/registry/install` | `video_studio_registry_install_api` |  |
| API_ROUTE | `/api/projects/{studio_id}/render` | `video_studio_render_api` |  |
| API_ROUTE | `/api/projects/{studio_id}/renders` | `video_studio_renders_api` |  |
| API_ROUTE | `/api/projects/{studio_id}/renders/file/{filename:path}` | `video_studio_render_file_api` |  |
| API_ROUTE | `/api/projects/{studio_id}/storyboard` | `video_studio_storyboard_api` |  |
| API_ROUTE | `/api/render/{job_id}` | `video_studio_render_job_api` |  |
| GET | `/api/render/{job_id}/progress` | `video_studio_render_progress_api` |  |
| DELETE | `/api/sessions/{session_id}/artifacts` | `delete_session_artifact` |  |
| GET | `/api/sessions/{session_id}/artifacts` | `session_artifacts` | Artifacts produced BY this session's runs (accumulated) — scopes the iterate |
| GET | `/api/settings/collaboration` | `get_collaboration_settings` |  |
| PUT | `/api/settings/collaboration` | `set_collaboration_settings` |  |
| GET | `/api/settings/higgsfield` | `get_higgsfield_settings` |  |
| PUT | `/api/settings/higgsfield` | `put_higgsfield_settings` |  |
| POST | `/api/settings/higgsfield/test` | `test_higgsfield_settings` |  |
| GET | `/api/settings/image-gen` | `get_image_gen_settings` | The saved image-gen config + provider metadata + codex readiness. |
| PUT | `/api/settings/image-gen` | `put_image_gen_settings` | Save the image-gen provider/model/key/baseUrl. An empty apiKey keeps the |
| POST | `/api/settings/image-gen/test` | `test_image_gen` | Test a provider. Codex/xAI → OAuth status; openai-compatible → endpoint probe. |
| GET | `/api/settings/permissions` | `get_permission_settings` | Auto-approve toggle: when on, agent permission prompts are approved |
| PUT | `/api/settings/permissions` | `set_permission_settings` |  |
| GET | `/api/settings/video-gen` | `get_video_gen_settings` |  |
| PUT | `/api/settings/video-gen` | `put_video_gen_settings` |  |
| POST | `/api/settings/video-gen/test` | `test_video_gen_settings` |  |
| API_ROUTE | `/api/video-studio/{token}/{slug}/{video_id}` | `video_studio_root` |  |
| API_ROUTE | `/api/video-studio/{token}/{slug}/{video_id}/{path:path}` | `video_studio_view` |  |


## routes/profiles.py

| Method | Path | Handler | Description |
| --- | --- | --- | --- |
| GET | `/api/commands/catalog` | `commands_catalog` |  |
| POST | `/api/commands/execute` | `commands_execute` |  |
| GET | `/api/profiles` | `list_profiles` |  |
| POST | `/api/profiles` | `create_profile` |  |
| DELETE | `/api/profiles/{profile_id}` | `delete_profile` |  |
| PATCH | `/api/profiles/{profile_id}` | `update_profile` |  |
| GET | `/api/runners/detect` | `runners_detect` |  |
| GET | `/api/runners/{runner_id}/capabilities` | `runner_capabilities` | Skills + MCP servers detected on the host for this runner (portable — |


## routes/projects.py

| Method | Path | Handler | Description |
| --- | --- | --- | --- |
| GET | `/api/fs/dirs` | `fs_dirs` | Browse directories under the configured link roots, to pick an existing |
| GET | `/api/projects` | `list_projects` |  |
| POST | `/api/projects` | `create_project` |  |
| POST | `/api/projects/link` | `link_project` | Register an EXISTING folder as a project (no scaffold). The project's |
| DELETE | `/api/projects/{slug}` | `delete_project` |  |
| GET | `/api/projects/{slug}` | `get_project` |  |
| PATCH | `/api/projects/{slug}` | `update_project` |  |


## routes/reviews.py

| Method | Path | Handler | Description |
| --- | --- | --- | --- |
| POST | `/api/message-reviews/{review_id}/ask-original` | `ask_original_to_revise` |  |
| POST | `/api/message-reviews/{review_id}/replace-answer` | `replace_answer_with_review` |  |
| POST | `/api/message-reviews/{review_id}/restore-original` | `restore_original_answer` |  |
| POST | `/api/message-reviews/{review_id}/use-revised` | `use_revised_review` |  |
| GET | `/api/messages/{message_id}/reviews` | `list_message_reviews` |  |
| POST | `/api/messages/{message_id}/reviews` | `create_message_review` |  |


## routes/update.py

| Method | Path | Handler | Description |
| --- | --- | --- | --- |
| POST | `/api/update/apply` | `update_apply` |  |
| POST | `/api/update/check` | `update_check` |  |
| GET | `/api/update/status` | `update_status` |  |


## routes/wiki.py

| Method | Path | Handler | Description |
| --- | --- | --- | --- |
| GET | `/api/projects/{slug}/wiki/all` | `project_wiki_all` |  |
| GET | `/api/wiki/all` | `wiki_all` |  |
| GET | `/api/wiki/file` | `wiki_read_file` |  |
| PUT | `/api/wiki/file` | `wiki_write_file` |  |
| DELETE | `/api/wiki/fs` | `wiki_delete` |  |
| POST | `/api/wiki/fs/mkdir` | `wiki_mkdir` |  |
| POST | `/api/wiki/fs/rename` | `wiki_rename` |  |
| GET | `/api/wiki/tree` | `wiki_tree` |  |


## routes/work.py

| Method | Path | Handler | Description |
| --- | --- | --- | --- |
| GET | `/api/jobs` | `list_jobs` |  |
| POST | `/api/jobs` | `create_job` |  |
| DELETE | `/api/jobs/{job_id}` | `delete_job` |  |
| GET | `/api/jobs/{job_id}` | `get_job` |  |
| POST | `/api/jobs/{job_id}/approve` | `approve_job` |  |
| POST | `/api/jobs/{job_id}/start` | `start_job` |  |
| GET | `/api/schedules` | `list_schedules` |  |
| POST | `/api/schedules` | `create_schedule` |  |
| DELETE | `/api/schedules/{schedule_id}` | `delete_schedule` |  |
| PATCH | `/api/schedules/{schedule_id}` | `update_schedule` |  |
| GET | `/api/workflows` | `list_workflows` |  |
| POST | `/api/workflows` | `create_workflow` |  |
| DELETE | `/api/workflows/{workflow_id}` | `delete_workflow` |  |
| GET | `/api/workflows/{workflow_id}` | `get_workflow` |  |
| PATCH | `/api/workflows/{workflow_id}` | `update_workflow` |  |
| POST | `/api/workflows/{workflow_id}/iterate` | `iterate_workflow` | Get-or-create the workflow's iterate/test chat — a sandbox session linked to |


## main.py (app-level)

| Method | Path | Handler | Description |
| --- | --- | --- | --- |
| GET | `/api/config` | `public_config` |  |
| GET | `/api/health` | `health` |  |
| POST | `/api/preview-auth` | `preview_auth` |  |


---
_Generated 2026-07-14 15:37 UTC._
