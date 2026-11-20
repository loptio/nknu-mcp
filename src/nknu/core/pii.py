"""Personal data in what this project writes itself: log lines, alerts and the found=false query log (redact), and the
files of the public repository (find_pii, which nknu check runs over every tracked file before a push).

- Announcement text is copied verbatim, honour lists and name lists included: a name the school did not mask is given
  as written, and text the school already masked stays as it is (owner 10-25: what the school publishes is the school's
  to control). Nothing here masks or drops school content.
- What a person types or a log line carries is redacted: mobile numbers, national ids, student numbers, personal email
  addresses (a numeric nknu.edu.tw mailbox is a student's; unit mailboxes are public contacts), and staff leave notes
  (陳O，請假).
"""

from __future__ import annotations

import re

_HAN = "一-鿿"
_MASKED = "OＯ○"

_GENERIC_LEAVE = {"學生", "学生", "教師", "教师", "職員", "职员", "員工", "员工", "同仁", "教職員", "师生", "師生", "線上",
                  "线上", "辦理", "办理", "申請", "申请"}
_PATTERNS = [
    # no letter or digit right before/after: hex hashes (uv.lock) hold such digit runs; spaced mobiles are common
    ("mobile", re.compile(r"(?<![0-9A-Za-z])09\d{2}[- ]?\d{3}[- ]?\d{3}(?![0-9A-Za-z])")),
    # \b fails next to 中文 (F-redact-gaps); a lower-case letter (typed ids) only as a whole word: hex hashes start so
    ("national_id", re.compile(r"(?<![A-Za-z0-9])(?:[A-Z][12]\d{8}(?![0-9])|[a-z][12]\d{8}(?![0-9A-Za-z]))")),
    ("student_id", re.compile(r"(?<![0-9A-Za-z])4\d{8}(?![0-9A-Za-z])")),
    ("email", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
    ("staff_leave", re.compile(rf"(?:^|[\s,，、。:：;；()（）\[\]])([{_HAN}{_MASKED}]{{2,4}})\s*[，,。.．_]?\s*(?:上午|下午)?\s*"
                               r"(?:請假|休假|公假|事假|病假|出差)")),
]


def _email_is_personal(addr: str) -> bool:
    local, _, domain = addr.lower().partition("@")
    if local in ("noreply", "no-reply") or domain in ("example.com", "example.org"):
        return False
    if len(local) >= 20 and local.isalnum():
        return False  # machine-generated ids such as ICS UIDs (…@google.com)
    if domain.endswith("nknu.edu.tw"):
        return local.isdigit()  # student mailboxes are numeric; unit mailboxes are public contacts
    return True


# full-width ASCII (a phone IME's ０９１２…, Ａ１２…) -> ASCII, one character for one, so match positions stay the same
_FULL_WIDTH = {c: c - 0xFEE0 for c in range(0xFF01, 0xFF5F)}


def find_pii(text: str) -> list[str]:
    """Return 'kind:match' strings for personal data that must not be published (the match as written in text)."""
    probe = text.translate(_FULL_WIDTH)
    hits = []
    for kind, rx in _PATTERNS:
        for m in rx.finditer(probe):
            if kind == "email" and not _email_is_personal(m.group()):
                continue
            if kind == "staff_leave" and m.group(1) in _GENERIC_LEAVE:
                continue
            if kind == "national_id" and re.search(r"wikidata|qid", probe[max(0, m.start() - 24):m.start()], re.IGNORECASE):
                continue
            hits.append(f"{kind}:{text[m.start():m.end()].strip()}")
    return hits


def redact(text: str) -> str:
    """Replace every find_pii hit with [MASKED] (logs, alerts, the found=false query log)."""
    for hit in find_pii(text):
        text = text.replace(hit.split(":", 1)[1], "[MASKED]")
    return text
