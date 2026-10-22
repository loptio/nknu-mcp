import ssl

from nknu.crawl.tls import NKNU_CA_PATH, NKNU_CA_SHA256, is_nknu_host, nknu_ssl_context, pem_sha256


def test_ctx_keeps_strict_adds_partial_chain():
    c = nknu_ssl_context()
    assert c.verify_flags & ssl.VERIFY_X509_STRICT and c.verify_flags & ssl.VERIFY_X509_PARTIAL_CHAIN
    assert c.verify_mode == ssl.CERT_REQUIRED and c.check_hostname


def test_bundled_ca_fingerprint():
    assert pem_sha256(NKNU_CA_PATH) == NKNU_CA_SHA256


def test_is_nknu_host():
    assert is_nknu_host("c.nknu.edu.tw") and is_nknu_host("nknu.edu.tw")
    assert not is_nknu_host("evilnknu.edu.tw") and not is_nknu_host("calendar.google.com")
