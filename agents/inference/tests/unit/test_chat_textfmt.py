"""채팅에 보낼 글 다듬기: 평문화와 길이 규칙 (Phase 5 설계 5.2절, FR-C21·C22·C24, AC-C6·C7).

순수 함수다. 평문화는 서식 문자만 걷어내고 내용은 지키며, 같은 입력에 두 번 적용해도 같다.
길이 규칙은 줄 경계에서 자르고 안내 줄을 붙이되 서버 상한을 넘지 않는다. 자동 게시는 하지 않는다(FR-I25).
"""

from __future__ import annotations

import pytest

from a4w_inference.channels.chat.textfmt import FitResult, fit_answer, to_plain_text

NOTICE = "나머지는 올리지 않았습니다. 필요하면 페이지에 올리라고 지시해 주세요."


# ── 평문화 ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("src,want", [
    ("**굵게** 말하기", "굵게 말하기"),
    ("__굵게__ 말하기", "굵게 말하기"),
    ("*기울임* 말하기", "기울임 말하기"),
    ("_기울임_ 말하기", "기울임 말하기"),
    ("~~취소~~ 됨", "취소 됨"),
    ("# 제목", "제목"),
    ("### 작은 제목 ###", "작은 제목"),
    ("`code` 입니다", "code 입니다"),
    ("> 인용문", "인용문"),
    ("<b>굵게</b> <i>기울임</i>", "굵게 기울임"),
    ("첫 줄<br>둘째 줄", "첫 줄\n둘째 줄"),
    ("<br/>시작", "\n시작".lstrip("\n")),
])
def test_T1_inline_markup_characters_are_removed_but_the_words_stay(src, want):
    assert to_plain_text(src) == want


def test_T2_links_and_images_keep_their_url_in_parentheses():
    assert to_plain_text("[회의록](https://wf.internal/pages/1) 참고") == "회의록 (https://wf.internal/pages/1) 참고"
    assert to_plain_text("![그림](https://wf.internal/a.png)") == "그림 (https://wf.internal/a.png)"


def test_T3_a_bare_url_is_left_alone():
    assert to_plain_text("보세요 https://wf.internal/pages/1?a=1&b=2 끝") == "보세요 https://wf.internal/pages/1?a=1&b=2 끝"


def test_T4_code_fences_are_dropped_but_the_code_lines_stay():
    src = "예시입니다.\n```python\nprint('hi')\nx = 1\n```\n끝."
    assert to_plain_text(src) == "예시입니다.\nprint('hi')\nx = 1\n끝."


def test_T5_bullets_become_plain_dashes_and_numbered_lists_stay():
    assert to_plain_text("* 하나\n* 둘\n+ 셋\n- 넷") == "- 하나\n- 둘\n- 셋\n- 넷"
    assert to_plain_text("1. 하나\n2. 둘") == "1. 하나\n2. 둘"


def test_T6_tables_lose_their_separator_row_and_outer_pipes():
    src = "| 이름 | 상태 |\n|---|---|\n| 회의록 | 완료 |"
    assert to_plain_text(src) == "이름 | 상태\n회의록 | 완료"


def test_T7_horizontal_rules_are_dropped():
    assert to_plain_text("위\n\n---\n\n아래") == "위\n\n아래"
    assert to_plain_text("위\n***\n아래") == "위\n아래"


def test_T8_text_that_only_looks_like_markup_is_not_damaged():
    for keep in ["2*3=6 이고 4*5=20", "my_tool_name 을 호출", "a < b 이고 c > d", "별표 * 하나만", "100% 완료_", "snake_case_word_here",
                 "페이지 3개를 찾았습니다.", "오전 10:30 회의 (3층)", "[대괄호] 만 있음", "a_b 그리고 c_d"]:
        assert to_plain_text(keep) == keep, keep


def test_T9_blank_lines_are_collapsed_and_edges_trimmed():
    assert to_plain_text("\n\n  하나  \n\n\n\n\n둘  \n\n") == "하나\n\n둘"


def test_T10_plain_korean_text_is_unchanged_and_the_function_is_idempotent():
    plain = "페이지를 3개 찾았습니다.\n- 회의록 (id 12)\n- 주간 보고 (id 15)"
    assert to_plain_text(plain) == plain
    marked = "# 결과\n**3개**를 찾았습니다.\n\n* [회의록](https://x/1)\n* `주간`\n\n| a | b |\n|---|---|\n| 1 | 2 |"
    once = to_plain_text(marked)
    assert "*" not in once and "#" not in once and "`" not in once and "](" not in once
    assert to_plain_text(once) == once


def test_T11_empty_and_whitespace_input_give_empty_text():
    assert to_plain_text("") == "" and to_plain_text("  \n \n") == ""


def test_T12_crlf_is_normalised_to_lf():
    assert to_plain_text("하나\r\n둘\r\n") == "하나\n둘"


# ── 길이 규칙 ─────────────────────────────────────────────────────────────

def fit(text, max_chars=40, hard=200, notice=NOTICE):
    return fit_answer(text, max_chars=max_chars, hard_max_chars=hard, notice=notice)


def test_L1_short_text_is_returned_as_is_without_the_notice():
    r = fit("짧은 답")
    assert r == FitResult(text="짧은 답", truncated=False)


def test_L2_text_exactly_at_the_limit_is_not_truncated():
    s = "가" * 40
    r = fit(s, max_chars=40)
    assert r.truncated is False and r.text == s


def test_L3_long_text_is_cut_on_a_line_boundary_and_the_notice_follows():
    text = "첫째 줄입니다\n둘째 줄입니다\n셋째 줄은 길어서 잘려 나갑니다 정말로 그렇습니다"
    r = fit(text, max_chars=30)
    body, _, tail = r.text.rpartition("\n")
    assert r.truncated is True and tail == NOTICE
    assert body == "첫째 줄입니다\n둘째 줄입니다"            # 마지막 줄은 통째로 빠진다(줄 중간에서 끊지 않는다)
    assert len(body) <= 30


def test_L4_the_kept_body_never_exceeds_max_chars():
    text = "\n".join(f"{i}번째 줄 내용" for i in range(100))
    for limit in (10, 25, 60, 120):
        r = fit(text, max_chars=limit, hard=1000)
        body = r.text.rsplit("\n", 1)[0]
        assert r.truncated and len(body) <= limit


def test_L5_a_single_long_line_is_cut_at_whitespace_when_possible():
    text = "alpha beta gamma delta epsilon zeta eta theta iota kappa lambda"
    r = fit(text, max_chars=25)
    body = r.text.rsplit("\n", 1)[0]
    assert r.truncated and text.startswith(body) and len(body) <= 25 and not body.endswith(" ")
    assert body in ("alpha beta gamma delta", "alpha beta gamma delta epsil"[:22]) or text[len(body)] == " "


def test_L6_a_single_unbroken_line_is_cut_by_characters():
    r = fit("가" * 100, max_chars=30)
    body = r.text.rsplit("\n", 1)[0]
    assert r.truncated and body == "가" * 30


def test_L7_the_result_never_exceeds_the_hard_limit():
    text = "줄\n" * 5000
    r = fit(text, max_chars=150, hard=200)
    assert len(r.text) <= 200


def test_L8_a_notice_that_cannot_fit_is_a_config_error():
    with pytest.raises(ValueError):
        fit_answer("x" * 100, max_chars=190, hard_max_chars=200, notice=NOTICE)      # 190 + 1 + len(notice) > 200
    with pytest.raises(ValueError):
        fit_answer("x", max_chars=0, hard_max_chars=200, notice=NOTICE)
    with pytest.raises(ValueError):
        fit_answer("x", max_chars=300, hard_max_chars=200, notice="")


def test_L9_the_config_check_applies_even_when_no_truncation_is_needed():
    """설정이 틀리면 짧은 답에서도 바로 드러나야 한다(어쩌다 긴 답이 올 때야 터지면 늦다)."""
    with pytest.raises(ValueError):
        fit_answer("짧음", max_chars=190, hard_max_chars=200, notice=NOTICE)


def test_L10_an_empty_answer_stays_empty_and_is_not_truncated():
    assert fit("") == FitResult(text="", truncated=False)


def test_L11_leading_blank_lines_do_not_leave_an_empty_body():
    r = fit("\n\n" + "가" * 100, max_chars=30)
    body = r.text.rsplit("\n", 1)[0]
    assert r.truncated and body.strip() != "" and r.text.endswith(NOTICE)


def test_L12_fit_does_not_strip_the_inner_blank_lines_it_keeps():
    text = "하나\n\n둘\n\n" + "셋" * 100
    r = fit(text, max_chars=20)
    assert r.text.startswith("하나\n\n둘\n") and r.text.endswith(NOTICE)
