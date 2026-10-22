"""Handshake only (no HTTP request), one connection per host. Run with: uv run pytest -m live tests/live"""

import socket

import pytest

from nknu.crawl.tls import nknu_ssl_context


@pytest.mark.live
@pytest.mark.parametrize("host", ["c.nknu.edu.tw", "sso.nknu.edu.tw", "lis.nknu.edu.tw"])
def test_strict_handshake_with_bundled_intermediate(host):
    with socket.create_connection((host, 443), timeout=15) as s, nknu_ssl_context().wrap_socket(s, server_hostname=host) as ss:
        assert ss.version() in ("TLSv1.2", "TLSv1.3")
