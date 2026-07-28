"""The First-Time Setup trigger must key off a signal that is actually absent
on a fresh install.

The repo ships 04-Projects/README.md, so "the 04-Projects folder doesn't
exist" is false from the first clone — a brand-new user who says hi gets no
onboarding. The one honest fresh-setup signal is the marker onboarding itself
writes on completion, the same one the session-start hook uses.
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_first_time_setup_trigger_uses_the_onboarding_marker() -> None:
    text = (REPO_ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    assert (
        "If `System/.onboarding-complete` doesn't exist, this is a fresh setup."
        in text
    )


def test_first_time_setup_trigger_never_regresses_to_folder_existence() -> None:
    text = (REPO_ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    assert "folder doesn't exist, this is a fresh setup" not in text


def test_shipped_projects_folder_proves_the_old_signal_is_dead() -> None:
    # The premise of the fix: this file ships with every install, so folder
    # existence can never again signal a fresh setup.
    assert (REPO_ROOT / "04-Projects" / "README.md").is_file()
