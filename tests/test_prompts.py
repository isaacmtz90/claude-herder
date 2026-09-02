"""Reading a blocked session's choices out of its terminal.

The registry says a session is `waiting` and why, but never what the choices
are. Those only exist on screen, so they have to be read back from the pane.
The captures below are real output from CLI 2.1.241.
"""
from claude_herder import prompts

REAL_TWO_OPTION = """\
> Run this bash command: echo herder-probe-three
  Echo probe string
  |- $ echo herder-probe-three
------------------------------------------------------------------
 Bash command
   echo herder-probe-three
   Echo probe string
 Permission rule Bash requires confirmation for this command.
 /permissions to update rules
 Do you want to proceed?
 ❯ 1. Yes
   2. No
 Esc to cancel · Tab to amend · ctrl+e to explain
"""

THREE_OPTION = """\
 Edit file
 Do you want to proceed?
 ❯ 1. Yes
   2. Yes, and don't ask again for this session
   3. No, and tell Claude what to do differently
 Esc to cancel
"""

NO_DIALOG = """\
> some prompt
  Ran 1 shell command
* Churned for 8s
------------------------------------------------------------------
>
------------------------------------------------------------------
  auto mode on
"""


class TestDetection:
    def test_a_real_two_option_dialog_is_detected(self):
        assert prompts.has_dialog(REAL_TWO_OPTION) is True

    def test_an_ordinary_pane_is_not_a_dialog(self):
        assert prompts.has_dialog(NO_DIALOG) is False

    def test_empty_input_is_not_a_dialog(self):
        assert prompts.has_dialog("") is False


class TestExtraction:
    def test_extracts_the_two_real_options_in_order(self):
        assert prompts.parse_options(REAL_TWO_OPTION) == ("Yes", "No")

    def test_extracts_three_options(self):
        assert prompts.parse_options(THREE_OPTION) == (
            "Yes",
            "Yes, and don't ask again for this session",
            "No, and tell Claude what to do differently",
        )

    def test_returns_nothing_when_no_dialog_is_present(self):
        assert prompts.parse_options(NO_DIALOG) == ()

    def test_the_cursor_glyph_is_stripped_from_the_label(self):
        assert "❯" not in "".join(prompts.parse_options(REAL_TWO_OPTION))

    def test_the_highlighted_index_is_reported(self):
        assert prompts.highlighted_index(REAL_TWO_OPTION) == 0

    def test_highlight_on_a_later_option_is_found(self):
        moved = THREE_OPTION.replace(" ❯ 1. Yes", "   1. Yes").replace(
            "   3. No,", " ❯ 3. No,")
        assert prompts.highlighted_index(moved) == 2

    def test_no_highlight_returns_none(self):
        assert prompts.highlighted_index(NO_DIALOG) is None


class TestRobustness:
    def test_the_most_recent_dialog_wins(self):
        # Scrollback can hold an older, already-answered prompt.
        stale = THREE_OPTION + "\n" + REAL_TWO_OPTION
        assert prompts.parse_options(stale) == ("Yes", "No")

    def test_option_text_containing_digits_is_kept_intact(self):
        pane = " Do you want to proceed?\n ❯ 1. Yes, run all 3 steps\n   2. No\n"
        assert prompts.parse_options(pane)[0] == "Yes, run all 3 steps"

    def test_a_footer_terminates_the_list(self):
        pane = (" Do you want to proceed?\n ❯ 1. Yes\n   2. No\n"
                " Esc to cancel\n   9. not an option\n")
        assert prompts.parse_options(pane) == ("Yes", "No")

    def test_a_blank_line_terminates_the_list(self):
        pane = " Do you want to proceed?\n ❯ 1. Yes\n   2. No\n\n   9. later\n"
        assert prompts.parse_options(pane) == ("Yes", "No")

    def test_out_of_sequence_numbering_is_rejected(self):
        # A wrapped line that happens to start with a digit must not be eaten.
        pane = " Do you want to proceed?\n ❯ 1. Yes\n   7. stray\n"
        assert prompts.parse_options(pane) == ("Yes",)

    def test_absurdly_long_labels_are_truncated(self):
        pane = " Do you want to proceed?\n ❯ 1. " + "x" * 500 + "\n"
        assert len(prompts.parse_options(pane)[0]) <= prompts.MAX_LABEL

    def test_more_options_than_a_menu_can_hold_are_capped(self):
        body = "".join(f"   {i}. option {i}\n" for i in range(1, 15))
        assert len(prompts.parse_options(" Do you want to proceed?\n" + body)) <= 9
