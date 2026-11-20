"""11-17 (the content session's ask: 「同一张图只留最大的那个尺寸附档，其余尺寸列成「按规则不抓」、不附档」): Wix lists one
picture under several addresses, the media's own (static.wixstatic.com/media/<id>) and its sizes (…/<id>/v1/fill/…);
the gender institute's old blog: 2,439 picture files of 653 pictures, each a file the content stage would read with
OCR. The export writes the largest of a picture's addresses with its file and lists the others as the rule, no file.
Other pictures as before. Made-up pictures."""

from __future__ import annotations

from nknu.archive import export
from tests.archive.test_export_step3_1108 import _with

MEDIA = "https://static.wixstatic.com/media/5e06f5_aaaa~mv2.jpg"
SMALL = MEDIA + "/v1/fill/w_147,h_98,al_c,q_80/a.jpg"
MID = MEDIA + "/v1/fill/w_454,h_303,al_c,q_80/a.jpg"
OTHER = "https://static.wixstatic.com/media/5e06f5_bbbb~mv2.png/v1/fill/w_100,h_100/b.png"
PLAIN = "https://c.nknu.edu.tw/aca/uploadfile/wix-test-photo.jpg"


def _jpeg(n: int) -> bytes:
    return b"\xff\xd8\xff\xe0" + b"x" * n


def test_a_wix_picture_is_written_once_at_its_largest(tmp_path):
    def build(a):
        for name, url, n in (("small", SMALL, 100), ("big", MEDIA, 900), ("mid", MID, 400), ("other", OTHER, 50),
                             ("plain", PLAIN, 30), ("plain2", PLAIN + "?v=2", 60)):
            a.file(name, url, _jpeg(n), "image/jpeg", kind="image", sniff="jpeg")

    _arch, _got, _out, rows, _readme = _with(tmp_path, build)
    assert rows[MEDIA]["本地档案"] and rows[MEDIA]["分类"] == "抓到了"
    for url in (SMALL, MID):
        assert (rows[url]["本地档案"], rows[url]["分类"]) == ("", "规则不抓"), url
        assert export.RULE_WHY["wix_size"] in rows[url]["原因"]
    assert rows[OTHER]["本地档案"] and rows[PLAIN]["本地档案"] and rows[PLAIN + "?v=2"]["本地档案"]


BLUR = MEDIA + "/v1/fill/w_1778,h_1000,fp_0.50_0.50,q_35,blur_30,enc_avif,quality_auto/a.jpg"
CLEAR = MEDIA + "/v1/fill/w_1088,h_612,fp_0.50_0.50,q_95,enc_avif,quality_auto/a.jpg"


def test_a_blurred_preview_is_never_the_one_kept_beside_a_clear_size(tmp_path):
    """11-18 (the content session's swap guard: 232 picture documents of gender.nknu.edu.tw left the trial library):
    Wix serves a blurred placeholder of a picture (…,q_35,blur_30,…) at more pixels and more bytes than the clear size
    the page shows. Kept as the largest, it gave OCR nothing to read while the clear one was listed as the rule. The
    largest of the clear addresses is the one kept; the blurred one is listed as another size."""
    def build(a):
        a.file("blur", BLUR, _jpeg(900), "image/jpeg", kind="image", sniff="jpeg")
        a.file("clear", CLEAR, _jpeg(400), "image/jpeg", kind="image", sniff="jpeg")
        a.file("small", SMALL, _jpeg(100), "image/jpeg", kind="image", sniff="jpeg")

    _arch, _got, _out, rows, _readme = _with(tmp_path, build)
    assert rows[CLEAR]["本地档案"] and rows[CLEAR]["分类"] == "抓到了"
    for url in (BLUR, SMALL):
        assert (rows[url]["本地档案"], rows[url]["分类"], rows[url]["原因代码"]) == ("", "规则不抓", "wix_size"), url
        assert CLEAR in rows[url]["备注"]


def test_a_picture_with_blurred_sizes_only_keeps_its_largest(tmp_path):
    def build(a):
        a.file("blur", BLUR, _jpeg(900), "image/jpeg", kind="image", sniff="jpeg")
        a.file("blur2", BLUR.replace("w_1778,h_1000", "w_400,h_225"), _jpeg(200), "image/jpeg", kind="image",
               sniff="jpeg")

    _arch, _got, _out, rows, _readme = _with(tmp_path, build)
    assert rows[BLUR]["本地档案"] and rows[BLUR]["分类"] == "抓到了"
    assert rows[BLUR.replace("w_1778,h_1000", "w_400,h_225")]["本地档案"] == ""
