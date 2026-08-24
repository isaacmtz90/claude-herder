-- Claude Herder helper.
--
-- This exists purely to own a TCC identity. Reaching a Ghostty tab needs
-- Accessibility permission, and a TCC grant attaches to a binary -- so granting
-- it to the daemon's interpreter would hand Accessibility to every Python
-- program the user ever runs. Compiled as an app, this script is the only thing
-- that needs the grant.
--
-- Build:  osacompile -o ClaudeHerderHelper.app ClaudeHerderHelper.applescript
-- Invoke: open -a <path to app> --args <title-tag> [text-to-type]

on run argv
	if (count of argv) is 0 then return "usage: <title-tag> [text]"
	set wanted to item 1 of argv
	set payload to ""
	if (count of argv) > 1 then set payload to item 2 of argv

	tell application "Ghostty" to activate
	delay 0.1

	tell application "System Events"
		tell process "Ghostty"
			set windowMenu to menu "Window" of menu bar 1
			-- `name`, not `title`: menu items expose the former.
			set hits to (every menu item of windowMenu whose name contains wanted)
			if (count of hits) is 0 then return "notfound"
			-- Two clicks: the first selects the tab, the second raises its window.
			click item 1 of hits
			delay 0.05
			click item 1 of hits
			delay 0.05
			try
				-- `activate` above can raise the wrong window.
				perform action "AXRaise" of window 1
			end try
		end tell
		if payload is not "" then
			delay 0.12
			keystroke payload
		end if
	end tell
	return "ok"
end run
