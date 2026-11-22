"""echo: how a piece of the agent's input is quoted in an error message (moved 11-22 from v1's envelope into
nknu.retrieval.server). It is cut by UTF-8 bytes, so neither a long input nor rare characters make a long message."""

from nknu.retrieval.server import ECHO_BYTES, echo


def test_a_short_input_is_quoted_whole():
    assert echo("unit") == "「unit」"
    assert echo(3) == "「3」"


def test_a_long_input_is_cut_to_the_byte_budget():
    out = echo("畢業門檻" * 20)
    assert out.startswith("「") and out.endswith("…」")
    assert len(out[1:-2].encode()) <= ECHO_BYTES


def test_non_printable_characters_become_question_marks():
    assert echo("a\nb\x00c") == "「a?b?c」"


def test_a_cut_never_splits_a_character():
    out = echo("㐀" * 30)  # 3 bytes each
    assert out[1:-2] == "㐀" * (ECHO_BYTES // 3)
