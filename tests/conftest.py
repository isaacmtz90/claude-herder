import pytest
from claude_herder.models import ConsoleKind, Session, SessionState


@pytest.fixture
def make_session():
    def _make(pid, *, sid=None, state=SessionState.IDLE, kind=ConsoleKind.INTERACTIVE,
              tty=None, entrypoint="cli", job_id=None, spare=False, name=None,
              proc_start="Mon Aug 24 00:27:05 2026", cwd="/Users/x/Repo", waiting_for=None):
        return Session(
            session_id=sid or f"sid-{pid}",
            pid=pid,
            proc_start=proc_start,
            cwd=cwd,
            name=name if name is not None else f"job-{pid}",
            state=state,
            kind=kind,
            entrypoint=entrypoint,
            waiting_for=waiting_for,
            job_id=job_id,
            tty=tty,
            spare=spare,
        )
    return _make
