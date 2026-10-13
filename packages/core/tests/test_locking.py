import pytest

from a4w_core.errors import A4WError
from a4w_core.locking import FileTargetLock, TargetLock


class Busy(A4WError):
    code = "BUSY"


def busy(key):
    return Busy(f"busy: {key}")


def test_is_a_target_lock(tmp_path):
    assert isinstance(FileTargetLock(tmp_path, busy=busy), TargetLock)


def test_second_holder_gets_the_given_error(tmp_path):
    with FileTargetLock(tmp_path, busy=busy).hold("wf"):
        with pytest.raises(Busy):
            with FileTargetLock(tmp_path, busy=busy).hold("wf"):
                pass


def test_keys_are_independent(tmp_path):
    lock = FileTargetLock(tmp_path, busy=busy)
    with lock.hold("wf"):
        with lock.hold("verify-wf"):
            pass


def test_released_after_error(tmp_path):
    lock = FileTargetLock(tmp_path, busy=busy)
    with pytest.raises(RuntimeError):
        with lock.hold("wf"):
            raise RuntimeError("x")
    with lock.hold("wf"):
        pass


def test_acquire_and_release_without_context(tmp_path):
    """작업 스레드에 잠금을 넘길 때 쓴다 (요청 스레드에서 잡고, 작업이 끝나면 푼다)."""
    lock = FileTargetLock(tmp_path, busy=busy)
    held = lock.acquire("wf")
    with pytest.raises(Busy):
        lock.acquire("wf")
    held.release()
    lock.acquire("wf").release()


def test_bad_key_is_rejected(tmp_path):
    with pytest.raises(ValueError):
        FileTargetLock(tmp_path, busy=busy).acquire("../x")
