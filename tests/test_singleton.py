"""Only one daemon may drive the pad."""
import os
from claude_herder.singleton import InstanceLock


def test_first_caller_gets_the_lock(tmp_path):
    assert InstanceLock(tmp_path / "d.lock").acquire() is True


def test_second_caller_is_refused(tmp_path):
    path = tmp_path / "d.lock"
    first = InstanceLock(path)
    assert first.acquire() is True
    assert InstanceLock(path).acquire() is False, \
        "two daemons on one pad fight over the serial port"
    first.release()


def test_releasing_lets_the_next_one_in(tmp_path):
    path = tmp_path / "d.lock"
    first = InstanceLock(path)
    first.acquire()
    first.release()
    assert InstanceLock(path).acquire() is True


def test_the_holder_records_its_pid_for_diagnostics(tmp_path):
    path = tmp_path / "d.lock"
    lock = InstanceLock(path)
    lock.acquire()
    assert lock.holder_pid() == os.getpid()
    lock.release()


def test_it_works_as_a_context_manager(tmp_path):
    path = tmp_path / "d.lock"
    with InstanceLock(path) as held:
        assert held.acquire() is True
        assert InstanceLock(path).acquire() is False
    assert InstanceLock(path).acquire() is True


def test_an_unwritable_location_does_not_block_startup(tmp_path):
    # Refusing to run because we cannot create a lock file would be worse than
    # running unlocked.
    assert InstanceLock(tmp_path / "no" / "such" / "dir" / "x" / "d.lock").acquire() \
        in (True, False)


def test_release_without_acquire_is_safe(tmp_path):
    InstanceLock(tmp_path / "d.lock").release()
