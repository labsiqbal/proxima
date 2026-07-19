## What

<!-- Describe the change. Link any related issues (Closes #N). -->

## Why

<!-- Explain the motivation. What problem does this solve or what goal does it advance? -->

## Testing

<!-- How did you verify this works? Check all that apply. -->

- [ ] `cd apps/api && uv run ruff check proxima_api tests` passes
- [ ] `cd apps/api && uv run python -m pytest -q` passes
- [ ] `cd apps/web && npx tsc --noEmit` passes
- [ ] Manually tested in the dev server (`bash scripts/dev`)
- [ ] No secrets, runtime data, or personal paths committed

## Screenshots

<!-- For UI changes, include before/after screenshots or a short screen recording. Delete this section if not applicable. -->
