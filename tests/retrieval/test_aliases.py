from pathlib import Path

import pytest

from nknu.retrieval.aliases import expand, load_aliases

SEED = Path(__file__).resolve().parents[2] / "config" / "aliases.yaml"
WITHDRAW = ["停修", "棄選", "退選"]


def test_load_aliases(tmp_path):
    p = tmp_path / "a.yaml"
    p.write_text("# seed\ngroups:\n  - [停修, 棄選, 退選]\n  - [ 碩博士 , 博碩士, 碩博士]\n", encoding="utf-8")
    assert load_aliases(p) == [WITHDRAW, ["碩博士", "博碩士"]]


def test_load_aliases_empty_file(tmp_path):
    p = tmp_path / "a.yaml"
    p.write_text("# nothing yet\n", encoding="utf-8")
    assert load_aliases(p) == []


@pytest.mark.parametrize(("body", "error"), [
    ("groups:\n  - [停修]\n", ValueError), ("groups:\n  - [停修, '!!']\n", ValueError),
    ("groups:\n  - [停修, ~]\n", ValueError), ("groups:\n  - 停修\n", TypeError), ("- [停修, 退選]\n", TypeError),
    ("groups: 停修\n", TypeError)])
def test_load_aliases_rejects_bad_groups(tmp_path, body, error):
    p = tmp_path / "a.yaml"
    p.write_text(body, encoding="utf-8")
    with pytest.raises(error):
        load_aliases(p)


def test_expand_adds_the_other_members():
    assert expand("退選", [WITHDRAW]) == (["停修", "弃选"], ["退選"])


def test_expand_traditional_or_simplified_query():
    assert expand("怎么退选", [WITHDRAW]) == (["停修", "弃选"], ["退選"])


def test_expand_no_match():
    assert expand("選課", [WITHDRAW]) == ([], [])
    assert expand("", [WITHDRAW]) == ([], [])


def test_expand_skips_tokens_already_in_the_query():
    extra, matched = expand("學分抵免", [["學分抵免", "抵免學分"]])
    assert matched == ["學分抵免"] and extra == ["免学"]


def test_expand_longest_member_wins():
    groups = [WITHDRAW, ["加退選", "加退選課"]]
    assert expand("加退選", groups) == (["选课"], ["加退選"])  # add/drop week is not 停修
    assert expand("加退選 和 退選", groups) == (["选课", "停修", "弃选"], ["加退選", "退選"])


def test_expand_ascii_member_is_a_whole_word():
    groups = [["單一登入", "SSO"]]
    assert expand("lasso", groups) == ([], [])
    assert expand("SSO單一登入", groups)[1] == ["SSO", "單一登入"]
    assert expand("sso 帳號", groups) == (["单一", "一登", "登入"], ["SSO"])


def test_seed_file():
    groups = load_aliases(SEED)
    assert 10 <= len(groups) <= 20
    assert any(set(WITHDRAW) <= set(g) for g in groups)
    assert any({"碩博士", "博碩士"} <= set(g) for g in groups)
