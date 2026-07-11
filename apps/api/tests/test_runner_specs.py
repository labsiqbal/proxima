from proxima_api.runner_specs import runner_spec, RUNNER_SPECS


def test_hermes_spec():
    s = runner_spec("hermes")
    assert s.spawn_argv[:2] == ["hermes", "acp"]
    assert s.home_env == "HERMES_HOME"


def test_claude_code_spec():
    s = runner_spec("claude-code")
    assert "claude-agent-acp" in " ".join(s.spawn_argv)
    assert s.home_env == "CLAUDE_CONFIG_DIR"
    assert s.binary == "claude"


def test_unknown_runner_falls_back_to_default():
    assert runner_spec("nope").id == "claude-code"


def test_registry_has_expected_runners():
    # Exact set: detection-only stubs (opencode/aider/cursor-agent) and the Gemini
    # runner were removed by owner decision 2026-07-10 — they must not come back
    # silently.
    assert set(RUNNER_SPECS.keys()) == {"hermes", "claude-code", "codex", "pi"}


def test_codex_spec():
    from proxima_api.runner_specs import runner_spec
    s = runner_spec("codex")
    assert "codex-acp" in " ".join(s.spawn_argv)
    assert s.home_env == "CODEX_HOME"
    assert s.binary == "codex"


def test_removed_runner_id_falls_back_and_is_not_selectable():
    # Old registry ids (removed 2026-07-10) behave like any unknown id.
    from proxima_api.runner_specs import runner_is_selectable, runner_spec
    assert runner_is_selectable("opencode") is False
    assert runner_spec("gemini").id == "claude-code"
