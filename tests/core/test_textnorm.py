from nknu.core.textnorm import alias_norm, fts_query, norm_text, tokenize


def test_two_char_query_tokens():
    assert tokenize("重訓") == ["重训"]


def test_bigrams_and_ascii():
    assert tokenize("和平 I-Read Space") == ["和平", "i-read", "space"]


def test_longer_run_bigrams():
    assert tokenize("重量訓練室") == ["重量", "量训", "训练", "练室"]


def test_single_char_kept():
    assert tokenize("館") == ["馆"]


def test_fullwidth_folded():
    assert norm_text("ＡＢＣ１２３") == "abc123"


def test_alias_norm_unifies():
    assert alias_norm("臺灣 光復節！") == alias_norm("台湾光复节")


def test_alias_norm_keeps_all_cjk():
    assert alias_norm("燕巢校區重量訓練室（歸燕食巢3樓）") == "燕巢校区重量训练室归燕食巢3楼"


def test_fts_query_quotes_tokens():
    assert fts_query(["重训", "i-read"]) == '"重训" OR "i-read"'
    assert fts_query([]) == ""
