"""Config loading. Everything version-coupled lives here, so it must not throw."""
from claude_herder import config as C


def test_missing_file_yields_usable_defaults(tmp_path):
    cfg = C.load(tmp_path / "absent.toml")
    assert cfg.approve_key == "1"
    assert len(cfg.actions) == 4


def test_deny_defaults_to_escape_not_a_digit(tmp_path):
    """Verified against CLI 2.1.241: the dialog is

        Do you want to proceed?
        > 1. Yes
          2. No

    Two options for some tools, three for others. In the three-option form "2"
    is "Yes, and don't ask again", so a fixed deny DIGIT can permanently approve
    the thing you meant to refuse. Escape cancels every shape of the dialog.
    """
    cfg = C.load(tmp_path / "absent.toml")
    assert cfg.deny_key == "@escape"
    assert not cfg.deny_key.isdigit()


def test_malformed_toml_falls_back_rather_than_crashing(tmp_path):
    path = tmp_path / "c.toml"
    path.write_text("this is not [ valid toml")
    assert C.load(path).approve_key == "1"


def test_permission_digits_are_overridable(tmp_path):
    path = tmp_path / "c.toml"
    path.write_text('[permissions]\napprove = "y"\ndeny = "n"\n')
    cfg = C.load(path)
    assert (cfg.approve_key, cfg.deny_key) == ("y", "n")


def test_timing_overrides_apply(tmp_path):
    path = tmp_path / "c.toml"
    path.write_text("[timing]\nsubmit_delay = 0.25\npoll_interval = 2.0\n")
    cfg = C.load(path)
    assert cfg.submit_delay == 0.25 and cfg.poll_interval == 2.0


def test_the_shipped_example_config_is_valid():
    from pathlib import Path
    example = Path(__file__).resolve().parents[1] / "config.example.toml"
    cfg = C.load(example)
    assert cfg.verified_against and cfg.actions
    # Slash commands in the shipped menu must submit, or a picked command just
    # sits on the prompt doing nothing.
    for action in cfg.actions:
        if action.text and action.text.startswith("/"):
            assert action.submit is True, action.label


def test_config_is_immutable():
    import pytest
    cfg = C.Config()
    with pytest.raises(Exception):
        cfg.approve_key = "9"
