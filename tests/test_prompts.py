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
        # Only "Yes" survives, and one option is not a choice, so nothing is
        # offered rather than a half-read menu.
        pane = " Do you want to proceed?\n ❯ 1. Yes\n   7. stray\n"
        assert prompts.parse_options(pane) == ()
        assert "stray" not in "".join(prompts.parse_options(pane))

    def test_absurdly_long_labels_are_truncated(self):
        pane = (" Do you want to proceed?\n ❯ 1. " + "x" * 500 + "\n"
                "   2. No\n")
        assert len(prompts.parse_options(pane)[0]) <= prompts.MAX_LABEL

    def test_a_single_option_is_not_a_choice(self):
        pane = " Do you want to proceed?\n ❯ 1. Yes\n"
        assert prompts.parse_options(pane) == ()

    def test_more_options_than_a_menu_can_hold_are_capped(self):
        body = "".join(f"   {i}. option {i}\n" for i in range(1, 15))
        assert len(prompts.parse_options(" Do you want to proceed?\n" + body)) <= 9


UNNUMBERED = """\
 Accessing workspace:
 /Users/x/Repo
 Quick safety check: Is this a project you created or one you trust?
 ❯ No, exit
   Yes, I trust this folder
"""

PLAN = """\
 Claude has written up a plan and is ready to execute. Would you like to proceed?
 ❯ 1. Yes, auto-accept edits
   2. Yes, manually approve edits
   3. No, keep planning
"""

CURLY = """\
 Do you want to proceed?
 ❯ 1. Yes
   2. Yes, and don\u2019t ask again for git status:* commands
   3. No
 esc to cancel
"""


class TestRealAnchors:
    def test_the_plan_approval_header_is_recognised(self):
        assert prompts.parse_options(PLAN)[2] == "No, keep planning"

    def test_unnumbered_dialogs_are_deliberately_refused(self):
        # Workspace-trust and bypass-permissions render with indexes hidden.
        # Their rows are indistinguishable from the prose above them, focus can
        # start on either row, and a wrong answer here is a security decision.
        # Refusing sends the user to the action menu instead, where "Focus"
        # raises the dialog to be read properly.
        assert prompts.parse_options(UNNUMBERED) == ()
        assert prompts.highlighted_index(UNNUMBERED) is None

    def test_a_curly_apostrophe_is_normalised(self):
        options = prompts.parse_options(CURLY)
        assert "don't ask again" in options[1]
        assert "\u2019" not in options[1]


class TestDecorations:
    def test_a_trailing_esc_hint_is_stripped(self):
        pane = " Do you want to proceed?\n ❯ 1. Yes\n   2. No, and tell Claude (esc)\n"
        assert prompts.parse_options(pane)[1] == "No, and tell Claude"

    def test_a_shift_tab_hint_is_stripped(self):
        pane = (" Do you want to proceed?\n ❯ 1. Yes\n"
                "   2. Yes, and switch to accept edits (shift+tab)\n")
        assert prompts.parse_options(pane)[1] == "Yes, and switch to accept edits"

    def test_a_percentage_in_a_label_is_kept(self):
        # "(37% used)" is real content, not a keybinding hint.
        pane = (" Do you want to proceed?\n ❯ 1. Yes, clear context (37% used)\n"
                "   2. No\n")
        assert "37%" in prompts.parse_options(pane)[0]


class TestScrolledLists:
    def test_a_scroll_indicator_is_not_an_option(self):
        pane = (" Do you want to proceed?\n ❯ 1. Yes\n   2. No\n \u2193 \n")
        assert prompts.parse_options(pane) == ("Yes", "No")

    def test_a_scrolled_list_is_reported_as_partial(self):
        pane = (" Do you want to proceed?\n   1. Yes\n \u2193 2. No\n")
        assert prompts.is_partial(pane) is True

    def test_a_complete_list_is_not_partial(self):
        assert prompts.is_partial(REAL_TWO_OPTION) is False


class TestUnparseableShapes:
    def test_a_multi_select_question_is_refused(self):
        # Checkbox tabs mean AskUserQuestion, which can be multi-select and
        # multi-question; modelling it as pick-one would be wrong.
        pane = " \u2610 Q1  \u2612 Q2\n Do you want to proceed?\n ❯ 1. A\n   2. B\n"
        assert prompts.parse_options(pane) == ()

    def test_screen_reader_mode_is_refused(self):
        pane = " 1. Yes\n 2. No\n Select with numbers [1-2] or up / down arrow keys.\n"
        assert prompts.parse_options(pane) == ()

    def test_a_yes_no_accessibility_prompt_is_refused(self):
        pane = " y. Yes, I trust this folder\n n. No, exit\n Enter y/n:\n"
        assert prompts.parse_options(pane) == ()


class TestLooksBlocked:
    """A cheap "is any dialog on screen?" check.

    Distinct from parse_options, which only understands numbered lists. A menu
    action ends by pressing Enter, and if an unexpected dialog is up that Enter
    answers it -- observed live: a workspace-trust prompt swallowed a /compact
    and quit the session by confirming its highlighted "No, exit".
    """

    def test_a_numbered_permission_dialog_is_blocked(self):
        assert prompts.looks_blocked(REAL_TWO_OPTION) is True

    def test_an_unnumbered_trust_dialog_is_blocked(self):
        # parse_options refuses this shape, but it is still a dialog.
        assert prompts.parse_options(UNNUMBERED) == ()
        assert prompts.looks_blocked(UNNUMBERED) is True

    def test_a_plan_approval_is_blocked(self):
        assert prompts.looks_blocked(PLAN) is True

    def test_an_ordinary_pane_is_not_blocked(self):
        assert prompts.looks_blocked(NO_DIALOG) is False

    def test_empty_text_is_not_blocked(self):
        assert prompts.looks_blocked("") is False
