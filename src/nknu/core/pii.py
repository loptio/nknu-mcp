"""Personal-data rules from the design doc §3.

- Announcement text is copied verbatim; text the school already masked stays as it is.
- Only honor-roll titles get names masked, in the school's own style: 陳宥余 -> 陳O余, 陳余 -> 陳O. A title is honor-roll
  content by its own words (is_honor_title: 恭賀, 榮獲, 名單 …), whatever list it is posted in (F-sso-crosspost-honor).
- A name is 2-3 characters starting with a common surname (3-4 with a compound one), found right before an honorific
  (陳宥余同學, 陳宥余 老師, 陳宥余、林宥余同學) or after a role word and before a verb (研究生陳宥余考取, 系友 陳宥余、林宥余參加).
  A span that does not look like a name is left as the school wrote it (榮升教授, 歷史系校友, 歡迎同學: F-honor-overmask);
  masking never invents a name.
"""

from __future__ import annotations

import re

_HAN = "一-鿿"
_MASKED = "OＯ○"
_N = rf"[{_HAN}{_MASKED}]"
_HONORIFIC = r"(?:同學|同学|老師|老师|教授|校友|主任|博士)"
_LIST_SEP = "、"  # not ，: a clause before a comma is not a list member (本國籍畢業生，請…校友)
# the run of characters right before an honorific (one space allowed between), or a list of runs joined by 、
_HONOR_RE = re.compile(rf"((?:{_N}+{_LIST_SEP}\s*)*)({_N}+?)(\s?)({_HONORIFIC})")
_ROLE = (r"研究生|畢業生|大學部|學生|同學|校友|老師|教師|教授|博士生|碩士生|博士班|碩士班|學長|學姐|學姊|系友|所友|選手|"
         r"隊員")
_VERB = r"考取|考上|榮獲|獲|榮升|通過|錄取|榮登|入選|當選|勇奪|奪|參加|參與|榮任|擔任|出任|代表|出席|赴"
# role word (a space, 、 or ： may follow) + names joined by 、 + verb: 研究生甲乙丙考取, 系友 甲乙丙、丁戊己參加; the names
# are lazy, so 林宥余榮獲 is 林宥余 + 榮獲, not 林宥余榮 + 獲
_ROLE_RE = re.compile(rf"({_ROLE})([\s、:：]*)((?:{_N}{{2,4}}?{_LIST_SEP}\s*)*{_N}{{2,4}}?)(?={_VERB})")
# a two-character name only right after one of these (化學系林O老師, 大三陳O同學), never inside a longer word (暨成立校友)
_BOUNDARY_END = re.compile(r"(?:系|所|班|組|學程|中心|院|生|友|級|屆|大[一二三四五]|碩[一二三]|博[一二三四]|師大|恭賀|恭喜|賀|"
                           r"本校|本系|本所|本院|系上)$")
# 取得獎勵 (an award scheme) holds 得獎; a 賽事名單 lists events, not people
HONOR_TITLE = re.compile(r"(?<!賽事)名單|榜單|榮獲|(?<!取)得獎|獲獎|獲頒|獲選|當選|入選|考取|錄取|榮升|榮登|恭賀|恭喜|狂賀|(?:^|[^\w])賀")

# common surnames in Taiwan (traditional, with the simplified forms of the most frequent); characters that mostly start
# ordinary words (安 全 成 申 時 常 應 華 花 畢 …) are left out on purpose: a name with such a surname stays readable
_SURNAMES = set(
    "陳林黃張李王吳劉蔡楊許鄭謝洪郭邱曾廖賴徐周葉蘇莊呂江何蕭羅高潘簡朱鍾游彭詹胡施沈余盧梁趙顏柯翁魏孫戴范方宋鄧杜傅"
    "侯曹薛丁卓阮馬董温溫唐藍石蔣古紀姚連馮歐程湯田康姜白汪鄒尤巫鐘黎涂凃龔嚴韓袁金童陸夏柳邵錢伍倪于譚駱熊任甘秦顧毛"
    "章史官萬俞雷粘饒闕凌崔尹孔辛辜陶段韋葛池孟褚殷麥賀賈莫文管裴樊房佘魯鮑郝穆塗邢蒲谷閻練盛鄔耿聶符祝繆岳籃喬龐翟"
    "牛鄞留季覃卜項喻滕焦車虞苗戚牟巴艾臧樓費屈宗幸衛尚靳祁諶桂沙欒宮刁邰冉甄晏荊酆薄苑敖霍隋戎寇姬郁卞冼冀湛鄺"
    "陈黄张刘杨许郑谢罗萧苏叶庄吕钟卢赵颜孙邓马温汤蓝蒋纪冯欧龚严韩陆钱谭骆顾万饶阙贺贾鲁鲍聂缪乔庞项卫宫邹吴"
)
_COMPOUND_SURNAMES = {"歐陽", "欧阳", "司馬", "司马", "諸葛", "诸葛", "上官", "東方", "东方", "皇甫", "公孫", "公孙", "慕容",
                      "長孫", "长孙", "宇文", "司徒", "夏侯", "令狐", "張簡", "张简", "范姜"}
# words that start with a surname character or end a span before an honorific, but are not names
_NOT_NAMES = {
    "全體", "全体", "各位", "新生", "畢業", "毕业", "應屆", "应届", "全校", "參賽", "参赛", "優秀", "优秀", "資深", "资深",
    "指導", "指导", "退休", "榮譽", "荣誉", "客座", "專任", "专任", "兼任", "所有", "在校", "學生", "学生", "導師", "导师",
    "獲獎", "获奖", "得獎", "得奖", "傑出", "杰出", "表揚", "表扬", "以上", "下列", "相關", "相关", "團隊", "团队",
    "代表隊", "隊伍", "副", "助理", "全體同", "研究",
    "榮升", "升等", "升任", "榮任", "連任", "新任", "現任", "前任", "卸任", "講座", "特聘", "名譽", "任課", "任教", "全國",
    "高中", "高職", "大學", "高師大", "高師", "師大", "高雄", "金門", "馬祖", "苗栗", "雲林", "陸生", "游泳", "田徑", "金牌",
    "金獎", "衛生", "管理", "顧問", "紀念", "周年", "週年", "簡報", "代表",
}
# a span holding a unit or place word is not a name (歷史系, 高雄市, 國文系王 in 國文系王主任)
_UNIT_CHARS = set("系所院班組處室館隊團社部局署市縣校會")


def is_honor_title(title: str) -> bool:
    """Honor-roll content (恭賀 … 榮獲, 錄取名單, 榜單): names in the title are masked, the body is never fetched."""
    return bool(HONOR_TITLE.search(title))


def mask_name(name: str) -> str:
    """Keep the first and last character, O for everything between; a two-character name keeps only the first."""
    if len(name) <= 1:
        return name
    if len(name) == 2:
        return name[0] + "O"
    return name[0] + "O" * (len(name) - 2) + name[-1]


def _is_masked(seg: str) -> bool:
    return any(c in _MASKED for c in seg)


def _not_a_name(s: str) -> bool:
    return not s or s in _NOT_NAMES or any(s.endswith(w) for w in _NOT_NAMES) or any(c in _UNIT_CHARS for c in s)


def _looks_like_name(s: str) -> bool:
    if _is_masked(s) or _not_a_name(s):
        return False
    if s[:2] in _COMPOUND_SURNAMES:
        return len(s) in (3, 4)
    return len(s) in (2, 3) and s[0] in _SURNAMES


def _tail_name(seg: str) -> tuple[str, str] | None:
    """(text kept verbatim, name) when seg ends with a name: 本系陳宥余, 理學院化學系林宥余, 大三陳余."""
    for n in (4, 3, 2):
        if len(seg) < n:
            continue
        before, name = seg[:-n], seg[-n:]
        if n == 4 and name[:2] not in _COMPOUND_SURNAMES:
            continue
        if not _looks_like_name(name):
            continue
        if n == 2 and before and not _BOUNDARY_END.search(before):
            continue
        return before, name
    return None


def mask_honor_names(title: str) -> str:
    def honor(m: re.Match[str]) -> str:
        parts = re.split(rf"({_LIST_SEP}\s*)", m.group(1) + m.group(2))
        for i in range(0, len(parts), 2):  # odd items are the separators
            split = _tail_name(parts[i])
            if split is not None:
                parts[i] = split[0] + mask_name(split[1])
        return "".join(parts) + m.group(3) + m.group(4)

    def role(m: re.Match[str]) -> str:
        parts = re.split(rf"({_LIST_SEP}\s*)", m.group(3))
        for i in range(0, len(parts), 2):
            if _looks_like_name(parts[i]):
                parts[i] = mask_name(parts[i])
        return m.group(1) + m.group(2) + "".join(parts)

    return _ROLE_RE.sub(role, _HONOR_RE.sub(honor, title))


_GENERIC_LEAVE = {"學生", "学生", "教師", "教师", "職員", "职员", "員工", "员工", "同仁", "教職員", "师生", "師生", "線上",
                  "线上", "辦理", "办理", "申請", "申请"}
_PATTERNS = [
    # no letter or digit right before/after: hex hashes (uv.lock) hold such digit runs; spaced mobiles are common
    ("mobile", re.compile(r"(?<![0-9A-Za-z])09\d{2}[- ]?\d{3}[- ]?\d{3}(?![0-9A-Za-z])")),
    ("national_id", re.compile(r"(?<![A-Za-z0-9])[A-Z][12]\d{8}(?![0-9])")),  # \b fails next to 中文 (F-redact-gaps)
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


def find_pii(text: str) -> list[str]:
    """Return 'kind:match' strings for personal data that must not be published."""
    hits = []
    for kind, rx in _PATTERNS:
        for m in rx.finditer(text):
            if kind == "email" and not _email_is_personal(m.group()):
                continue
            if kind == "staff_leave" and m.group(1) in _GENERIC_LEAVE:
                continue
            if kind == "national_id" and re.search(r"wikidata|qid", text[max(0, m.start() - 24):m.start()], re.IGNORECASE):
                continue
            hits.append(f"{kind}:{m.group().strip()}")
    return hits


def redact(text: str) -> str:
    """Replace every find_pii hit with [MASKED] (logs, alerts, the found=false query log)."""
    for hit in find_pii(text):
        text = text.replace(hit.split(":", 1)[1], "[MASKED]")
    return text
