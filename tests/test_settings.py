"""Settings come from the environment (/etc/nknu/env on the lab machine, copied once from deploy/env.example)."""

from nknu.settings import REPO_ROOT, load_settings


def test_trust_cf_header_defaults_on():
    assert load_settings({}).trust_cf_header is True
    assert load_settings({"NKNU_TRUST_CF_HEADER": ""}).trust_cf_header is True  # an empty line in the env file
    assert load_settings({"NKNU_TRUST_CF_HEADER": "1"}).trust_cf_header is True
    for off in ("0", " 0 ", "false", "No", "off"):
        assert load_settings({"NKNU_TRUST_CF_HEADER": off}).trust_cf_header is False, off


def test_env_example_lists_every_setting():
    read: list[str] = []

    class Spy(dict):
        def get(self, key, default=None):
            read.append(key)
            return super().get(key, default)

        def __getitem__(self, key):
            read.append(key)
            return super().__getitem__(key)

    load_settings(Spy())
    lines = (REPO_ROOT / "deploy" / "env.example").read_text(encoding="utf-8").splitlines()
    listed = [ln.split("=", 1)[0] for ln in lines if ln.strip() and not ln.lstrip().startswith("#")]
    assert len(listed) == len(set(listed))
    assert sorted(set(read)) == sorted(listed)
