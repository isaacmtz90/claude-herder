"""Background jobs: watchable, not typeable.

A background session has no controlling terminal, so no keystroke can reach it
-- there is simply nowhere to send one. Rather than let control keys silently
do nothing, this adapter reports `can_control() == False` (which the renderer
turns into a dimmer key) and offers the one thing that is genuinely useful:
opening the job's output so you can read what it did.
"""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)


def jobs_dir() -> Path:
    config = os.environ.get("CLAUDE_CONFIG_DIR") or str(Path.home() / ".claude")
    return Path(config) / "jobs"


class BgJobAdapter:
    name = "bgjob"

    def can_control(self) -> bool:
        return False

    def owns(self, tty: str | None) -> bool:
        return tty is None

    def send_text(self, tty: str, text: str, submit: bool = False) -> bool:
        return False

    def send_key(self, tty: str, key: str) -> bool:
        return False

    def focus(self, tty: str) -> bool:
        return False

    def open_output(self, job_id: str | None) -> bool:
        """Reveal the job's directory so its timeline and output are readable."""
        if not job_id:
            return False
        target = jobs_dir() / job_id
        if not target.exists():
            log.warning("no job directory at %s", target)
            return False
        try:
            subprocess.run(["open", str(target)], timeout=8, check=False)
            return True
        except (OSError, subprocess.SubprocessError) as exc:
            log.warning("cannot open %s: %s", target, exc)
            return False
