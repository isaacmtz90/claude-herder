"""Joining a terminal tab to the Claude session it is displaying.

The registry cannot pair a fleet attach console with its worker: the console's
pid is not in the registry, and the worker has no tty. But Claude Code sets the
terminal title to the session name, and iTerm2 exposes each tab's title -- so
the title is the join key, across two supported surfaces.
"""
from claude_herder import titles


class TestNormalise:
    def test_strips_the_leading_state_glyph(self):
        assert titles.normalise("◐ detect adafruit keyboard input (claude)") \
            == "detect adafruit keyboard input"

    def test_strips_the_trailing_program_name(self):
        assert titles.normalise("settings-migration v2 testing (claude)") \
            == "settings-migration v2 testing"

    def test_strips_a_node_program_suffix(self):
        assert titles.normalise("✳ Review function (node)") == "Review function"

    def test_leaves_a_plain_title_alone(self):
        assert titles.normalise("investigate slow build failures") \
            == "investigate slow build failures"

    def test_handles_empty_and_whitespace(self):
        assert titles.normalise("") == ""
        assert titles.normalise("   ") == ""

    def test_shell_titles_reduce_to_their_directory(self):
        assert titles.normalise("..ments-backend (-zsh)") == "..ments-backend"


class TestMatching:
    NAMES = (
        "detect adafruit keyboard input",
        "settings-migration v2 testing",
        "investigate slow build failures",
    )

    def test_exact_title_matches_its_session(self):
        assert titles.match("◐ detect adafruit keyboard input (claude)", self.NAMES) \
            == "detect adafruit keyboard input"

    def test_matching_ignores_case(self):
        assert titles.match("SETTINGS-MIGRATION V2 TESTING (claude)", self.NAMES) \
            == "settings-migration v2 testing"

    def test_truncated_title_still_matches_by_suffix(self):
        # iTerm2 elides long titles with a leading "..".
        assert titles.match("..adafruit keyboard input (claude)", self.NAMES) \
            == "detect adafruit keyboard input"

    def test_registry_name_truncation_is_tolerated(self):
        # The registry name and the title can disagree on trailing characters.
        assert titles.match("investigate slow build failure (claude)", self.NAMES) \
            == "investigate slow build failures"

    def test_unrelated_title_matches_nothing(self):
        assert titles.match("some random shell (-zsh)", self.NAMES) is None

    def test_ambiguous_title_is_refused_rather_than_guessed(self):
        names = ("deploy staging", "deploy production")
        assert titles.match("deploy (claude)", names) is None

    def test_empty_title_matches_nothing(self):
        assert titles.match("", self.NAMES) is None

    def test_empty_candidate_names_are_ignored(self):
        assert titles.match("anything (claude)", ("", "  ")) is None


class TestFalsePositives:
    """A wrong join sends Approve to the wrong session, so matching must be
    stricter than "shares some characters". These are real titles observed on
    the development machine."""

    def test_a_plain_shell_tab_does_not_match_a_similar_session_name(self):
        # "..ments-backend" is a zsh tab, not a Claude session. It shares a long
        # substring with the session name "payments-backend-77" and must still
        # be refused.
        assert titles.match("..ments-backend (-zsh)", ("payments-backend-77",)) is None

    def test_front_elided_titles_match_by_suffix_not_substring(self):
        # A leading ".." means characters were dropped from the FRONT, so the
        # visible text is the END of the real title.
        assert titles.match("..adafruit keyboard input (claude)",
                            ("detect adafruit keyboard input",)) \
            == "detect adafruit keyboard input"
        assert titles.match("..backend (-zsh)", ("backend-tools-77",)) is None

    def test_npm_and_other_program_tabs_match_nothing(self):
        names = ("investigate slow build failures", "payments-20")
        assert titles.match("npm (node)", names) is None
        assert titles.match("..onent-library (-zsh)", names) is None
