# Locked repo and hidden source policy

> **Scope note.** Proxima is **single-user** today: one owner, no in-app
> accounts/roles/membership. There is no "normal app user" to hide source from — the
> owner already has full machine access. So the elaborate locked-repo machinery below
> is **future / secure-mode only** — relevant *only if* Proxima is ever hosted for
> other, less-trusted users. What still applies now is the untrusted-agent rule in the
> next section.

## What applies now (single-user)

Even with one owner, treat a prompt-injected **agent run** as untrusted — it must not
read the app's source, secrets, or runtime data just because a prompt asked:

- Keep project workspace roots under the configured workspace; don't set a project
  root to a source/runtime directory.
- Don't add UI/API routes that browse arbitrary server paths.
- Keep `~/.config/proxima/` (env/secrets) and `~/.local/share/proxima/`
  (db, hermes-profiles, backups) out of runner/file-API reach.
- Prompt text can never grant access — authorization comes from the resolved run
  context. See [prompt-injection-hardening.md](prompt-injection-hardening.md) and
  [security-boundaries.md](security-boundaries.md).

## Future: locked repos (only if hosting for others)

If Proxima ever runs for multiple, less-trusted users, a "locked repo" would be a
source/runtime path hidden from a user's file browsing + runner context unless
explicitly granted. This would need:

- a `locked_repos` table (path, default visibility, edit policy, reason) + per-project
  grant overrides with expiry;
- an admin UI for grants/revocations;
- a path-policy service that builds an allowlisted context per run;
- audit events for grants/revocations/access attempts;
- tests for path traversal + prompt-injection unlock attempts.

None of this exists in the single-user code path and should not be documented as if it
does.
