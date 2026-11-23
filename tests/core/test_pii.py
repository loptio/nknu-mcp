from nknu.core.pii import find_pii, redact


def test_find_pii_hits():
    assert find_pii("辛O癸請假")
    assert find_pii("聯絡 0912-345-678")
    assert find_pii("身分證 A123456789")
    assert find_pii("學號 400000000")
    assert find_pii("寄到 someone@gmail.com")


def test_find_pii_ignores_office_contacts():
    assert not find_pii("體育室 07-7172930 分機 1551，信箱 noreply@nknu.edu.tw")


def test_generated_ids_are_not_emails():
    assert find_pii("UID:0l8dnv5qpj1p4871b3bf62b8vu@google.com") == []
    assert find_pii("someone.name@google.com")


def test_wikidata_qid_is_not_a_national_id():
    assert find_pii("wikidata_qid: Q127164121") == []
    assert find_pii("身分證 Q123456789")


def test_national_id_next_to_chinese_and_spaced_mobile():
    # F-redact-gaps
    assert find_pii("身分證A123456789號碼")
    assert find_pii("手機0912 345 678")
    assert find_pii("手機 0912 345678")
    assert redact("我的身分證A123456789，手機0912 345 678") == "我的身分證[MASKED]，手機[MASKED]"


def test_hex_hashes_are_not_phone_or_student_numbers():
    # F-check-scope: uv.lock hashes once read as mobiles and student numbers
    assert find_pii("sha256:c7e58ce09192557605d8bbd") == []
    assert find_pii("05221a5d8b3c476330730b3adb4") == []


def test_full_width_digits_and_a_lowercase_id_letter_are_found():
    # review of G7: a phone IME types full-width digits, and an id is often typed in lower case (misses.jsonl)
    assert redact("我的學號４０００００００１怎麼查成績") == "我的學號[MASKED]怎麼查成績"
    assert redact("手機０９１２３４５６７８聯絡誰") == "手機[MASKED]聯絡誰"
    assert redact("身分證a123456789") == "身分證[MASKED]"
    assert find_pii("sha256:a123456789bcdef0") == []  # a hash that starts like an id is not one
