"""TLS for *.nknu.edu.tw.

TWCA Global Root CA (in certifi) has no Subject Key Identifier, which Python 3.13's VERIFY_X509_STRICT rejects.
We keep STRICT, bundle the intermediate the school's servers send (TWCA Secure SSL Certification Authority, the 2023G3
generation; verified offline against certifi's TWCA Global Root CA) and let VERIFY_X509_PARTIAL_CHAIN anchor the chain there.
Never verify=False. Intermediate expires 2030-10-16; the school's leaf expires 2027-02-10 (recheck on renewal).
"""

from __future__ import annotations

import hashlib
import ssl
from pathlib import Path

import certifi

from nknu.settings import REPO_ROOT

NKNU_CA_PATH = REPO_ROOT / "certs" / "twca-secure-ssl-ca-2023g3.pem"
NKNU_CA_SHA256 = "1a2c75fd096e0499e9ff6ac74e526f61eaae3edfc8c2ea4436fee0c24d8b7d0e"


def pem_sha256(path: Path) -> str:
    return hashlib.sha256(ssl.PEM_cert_to_DER_cert(Path(path).read_text())).hexdigest()


def is_nknu_host(host: str | None) -> bool:
    return bool(host) and (host == "nknu.edu.tw" or host.endswith(".nknu.edu.tw"))


def nknu_ssl_context(cafile: Path = NKNU_CA_PATH) -> ssl.SSLContext:
    ctx = ssl.create_default_context(cafile=certifi.where())
    ctx.load_verify_locations(cafile=str(cafile))
    ctx.verify_flags |= ssl.VERIFY_X509_PARTIAL_CHAIN
    if not ctx.verify_flags & ssl.VERIFY_X509_STRICT:
        raise RuntimeError("VERIFY_X509_STRICT must stay on")
    return ctx


def default_ssl_context() -> ssl.SSLContext:
    return ssl.create_default_context(cafile=certifi.where())
