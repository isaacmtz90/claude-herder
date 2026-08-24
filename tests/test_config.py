"""Config loading. Everything version-coupled lives here, so it must not throw."""
from claude_herder import config as C


def test_missing_file_yields_usable_defaults(tmp_path):
    cfg = C.load(tmp_path / "absent.toml")
    assert cfg.approve_key == "1"
    assert len(cfg.canned) == 3


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


def test_canned_keys_are_read_in_order(tmp_path):
    path = tmp_path / "c.toml"
    path.write_text(
        '[[canned]]\nlabel="a"\ntext="/one"\nsubmit=true\n'
        '[[canned]]\nlabel="b"\ntext="/two"\n')
    cfg = C.load(path)
    assert [c.text for c in cfg.canned] == ["/one", "/two"]
    assert (cfg.canned[0].submit, cfg.canned[1].submit) == (True, False)


def test_more_than_three_canned_keys_are_truncated(tmp_path):
    path = tmp_path / "c.toml"
    path.write_text("".join(
        f'[[canned]]\nlabel="k{i}"\ntext="/{i}"\n' for i in range(6)))
    assert len(C.load(path).canned) == 3


def test_canned_entries_without_text_are_skipped(tmp_path):
    path = tmp_path / "c.toml"
    path.write_text('[[canned]]\nlabel="broken"\n[[canned]]\nlabel="ok"\ntext="/x"\n')
    assert [c.text for c in C.load(path).canned] == ["/x"]


def test_timing_overrides_apply(tmp_path):
    path = tmp_path / "c.toml"
    path.write_text("[timing]\nsubmit_delay = 0.25\npoll_interval = 2.0\n")
    cfg = C.load(path)
    assert cfg.submit_delay == 0.25 and cfg.poll_interval == 2.0


def test_the_shipped_example_config_is_valid():
    from pathlib import Path
    example = Path(__file__).resolve().parents[1] / "config.example.toml"
    cfg = C.load(example)
    assert cfg.verified_against and len(cfg.canned) == 3
    # The plan-mode key must carry a real escape sequence, not the literal text.
    assert "\x1b" in cfg.canned[0].text


def test_config_is_immutable():
    import pytest
    cfg = C.Config()
    with pytest.raises(Exception):
        cfg.approve_key = "9"
