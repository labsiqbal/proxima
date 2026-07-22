# Proxima capability bundle (`bundled-skills/`)

Skills and tool recommendations that ship with Proxima and activate for every
agent profile out of the box (T8, Phase 1 slice 9).

## How it works (content-pluggable - no skill list in code)

- **Any folder here with a `SKILL.md` is a bundled skill.** The backend scans this
  directory (`capabilities.py:detect_bundled_skills`) and registers each skill as a
  second source in the existing skill-symlink mechanism, alongside the skills the
  runner already has on the host. Adding or removing a skill folder is the whole
  change - no code edit, no registry.
- Bundled skills get the id `bundled/<folder>` and are symlinked into each profile
  home's skills dir (per-runner subpath, same as host skills). Every runner with a
  skills dir (claude-code, codex, hermes, pi) picks them up.
- **Opt-out per profile:** the existing capabilities selection JSON on a profile
  (`profiles.capabilities`) controls bundled skills exactly like host skills - omit
  `bundled/<name>` from the `skills` list to disable it for that profile.
- **Live-home mode** (`PROXIMA_CLAUDE_LIVE_HOME=1`): the agent home is the real
  `~/.claude`, so Proxima seeds nothing and bundled skills are NOT symlinked - the
  user's own setup rules.
- `recommended-tools.json` is the bundle's tool advisory list: CLIs Proxima probes
  for on PATH at run setup and advertises in the agent preamble when present.
  Proxima never ships or installs binaries - detect-and-advertise only. Edit the
  JSON to change the toolbelt; missing tools surface as a quiet hint in Settings.

## Content

- `masterplan/` - turn a product idea into an execution-ready masterplan package.
  Vendored from [labsiqbal/masterplan](https://github.com/labsiqbal/masterplan)
  (MIT); see `masterplan/PROVENANCE.md` for the exact commit and refresh steps.
  Do not edit the vendored content in place - change it upstream and re-vendor.

## Adding a bundled skill

1. Create `bundled-skills/<name>/SKILL.md` (YAML frontmatter with `name` +
   `description`, then the skill body), plus any support files.
2. If vendored from elsewhere, add a `PROVENANCE.md` sidecar (source, commit,
   license) and the upstream `LICENSE` file.
3. Nothing else - it is detected on the next capability apply.
