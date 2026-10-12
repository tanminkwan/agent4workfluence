import multiprocessing

import pytest

from a4w_core.errors import DetectInProgress
from a4w_detection.storage.file_lock import FileTargetLock


def test_second_holder_is_rejected(tmp_path):
    lock = FileTargetLock(tmp_path)
    with lock.hold("wf"):
        with pytest.raises(DetectInProgress):
            with FileTargetLock(tmp_path).hold("wf"):
                pass


def test_different_targets_do_not_block(tmp_path):
    lock = FileTargetLock(tmp_path)
    with lock.hold("a"):
        with lock.hold("b"):
            pass


def test_released_after_exit_and_after_error(tmp_path):
    lock = FileTargetLock(tmp_path)
    with pytest.raises(RuntimeError):
        with lock.hold("wf"):
            raise RuntimeError("x")
    with lock.hold("wf"):
        pass


def _try(path, q):
    try:
        with FileTargetLock(path).hold("wf"):
            q.put("acquired")
    except DetectInProgress:
        q.put("busy")


def test_other_process_is_rejected(tmp_path):
    q = multiprocessing.Queue()
    with FileTargetLock(tmp_path).hold("wf"):
        p = multiprocessing.Process(target=_try, args=(tmp_path, q))
        p.start(); p.join(10)
    assert q.get(timeout=5) == "busy"
