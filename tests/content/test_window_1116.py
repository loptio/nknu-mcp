"""spec 2026-11-16 §三 (plan 2026-11-16 T1): the embedder reads EMBED_MAX_SEQ tokens instead of 512, and the vector
cache tells the vectors of the two windows apart only where they can differ: a text of SAFE_CHARS characters or fewer
is inside 512 tokens whatever the window, so its cached vector stays good."""
from __future__ import annotations

from nknu.content.embed import SAFE_CHARS, VecCache, encode_cached, vec_key
from nknu.core import models
from tests.content.test_embed import FakeEmbedder

SHORT = "申請停修的期限是第十二週。"
LONG = "學" * (SAFE_CHARS + 1)


def test_a_short_text_has_one_key_whatever_the_window():
    assert SAFE_CHARS == 500
    assert vec_key("m", "r", SHORT) == vec_key("m", "r", SHORT, models.BASE_SEQ) == vec_key("m", "r", SHORT, 1024)
    assert vec_key("m", "r", "學" * SAFE_CHARS, 1024) == vec_key("m", "r", "學" * SAFE_CHARS)


def test_a_long_text_has_a_key_of_its_window():
    assert vec_key("m", "r", LONG, 1024) != vec_key("m", "r", LONG)
    assert vec_key("m", "r", LONG, models.BASE_SEQ) == vec_key("m", "r", LONG)  # 512: the key of every earlier build
    assert vec_key("m", "r", LONG, 1024) != vec_key("m", "r", LONG, 2048)


def test_a_wider_embedder_encodes_only_the_long_texts_again(tmp_path):
    with VecCache(tmp_path / "vec.sqlite") as cache:
        old = FakeEmbedder()  # no max_seq: an embedder of before, 512
        assert encode_cached(old, [SHORT, LONG], cache=cache)[1:] == (0, 2)
        wide = FakeEmbedder()
        wide.max_seq = 1024
        _, cached, encoded = encode_cached(wide, [SHORT, LONG], cache=cache)
        assert (cached, encoded) == (1, 1) and wide.texts == [LONG]
        assert encode_cached(wide, [SHORT, LONG], cache=cache)[1:] == (2, 0)  # and both are cached from then on
        assert encode_cached(old, [SHORT, LONG], cache=cache)[1:] == (2, 0)  # the 512 vectors are still there
