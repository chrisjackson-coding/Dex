"""Keep the agent closeout guidance honest about release state."""

from __future__ import annotations

import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
GUIDANCE = (REPO_ROOT / "AGENTS.md").read_text(encoding="utf-8")
NORMALIZED_GUIDANCE = re.sub(r"\s+", " ", GUIDANCE)


def test_agent_guidance_requires_all_delivery_states() -> None:
    assert "### Delivery-state honesty" in GUIDANCE
    assert "Delivery state:" in GUIDANCE
    for state in (
        "implemented=",
        "committed=",
        "pushed=",
        "merged=",
        "published/live=",
    ):
        assert state in GUIDANCE


def test_agent_guidance_separates_not_live_from_thread_completion() -> None:
    assert "Not live: deployment was explicitly excluded." in NORMALIZED_GUIDANCE
    assert "Never infer `published/live=yes`" in GUIDANCE
    assert 'Do not write “Nothing is running, queued, or waiting on you”' in GUIDANCE
