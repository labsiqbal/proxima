"""Runner banner/skills-dump stripping for collab cards and synthesis prompts."""

from proxima_api.prompt_collaborations import (
    build_debate_synthesis_prompt,
    strip_runner_preamble,
)


def test_strip_runner_preamble_drops_pi_skills_banner():
    raw = (
        "pi v0.80.10 --- ## Skills - /home/user/.pi/agent/skills/tdd/SKILL.md "
        "- /home/user/.agents/skills/qa/SKILL.md --- New version available: v0.81.1 "
        "(installed v0.80.10). Run: `npm i -g @earendil-works/pi-coding-agent` "
        "## Position **Use SQLite.** ## Rebuttal Files reinvent a worse DB."
    )
    assert strip_runner_preamble(raw) == (
        "## Position **Use SQLite.** ## Rebuttal Files reinvent a worse DB."
    )


def test_strip_runner_preamble_leaves_clean_answers():
    body = "## Position\n\nPlain text wins for tiny lists."
    assert strip_runner_preamble(body) == body
    assert strip_runner_preamble("") == ""
    assert strip_runner_preamble(None) == ""


def test_debate_synthesis_prompt_strips_child_noise():
    polluted = (
        "pi v0.80.10 --- ## Skills - /tmp/x/SKILL.md --- "
        "## Position **Use SQLite for structured todos.**"
    )
    prompt = build_debate_synthesis_prompt(
        "text vs sqlite?",
        [
            {
                "role": "rebuttal",
                "profile_name": "Pi",
                "runner_id": "pi",
                "content": polluted,
            }
        ],
    )
    assert "SKILL.md" not in prompt
    assert "pi v0.80.10" not in prompt
    assert "## Position **Use SQLite for structured todos.**" in prompt
