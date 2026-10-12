from a4w_detection.textdiff import TextDiffer


def test_unified_diff_with_context():
    out = TextDiffer(context_lines=1).diff("a\nb\nc\nd\n", "a\nb\nX\nd\n", "old", "new")
    assert out.splitlines()[:2] == ["--- old", "+++ new"]
    assert "-c" in out and "+X" in out
    assert " a" not in out.splitlines()  # 앞뒤 1줄만


def test_first_run_diff_from_empty():
    out = TextDiffer(context_lines=3).diff(None, "a\n", "empty", "new")
    assert "+a" in out


def test_identical_is_empty():
    assert TextDiffer(context_lines=3).diff("a\n", "a\n", "o", "n") == ""
