"""Site lists for crawler tests (round1.Plan): a small explicit one, and an open one that takes any school site as a
round-1 site (本系重要), so that a test about politeness, robots.txt or the network need not list its made-up hosts."""

from __future__ import annotations

from urllib.parse import urlsplit

from nknu.archive import round1, scope


def site(key: str, rnd: str = "1", imp: str | None = "本系重要", **kw) -> round1.Site:
    home = kw.pop("home", f"https://{key}/")
    return round1.Site(site=key, round=rnd, imp=imp if rnd == "1" else None, home=home, **kw)


def plan(*sites: round1.Site, extra=(), login_checks=(), round2_pages=()) -> round1.Plan:
    return round1.Plan({s.site: s for s in sites}, tuple(extra), tuple(login_checks), "test-plan", tuple(round2_pages))


class OpenPlan(round1.Plan):
    """Every school site key is a round-1 site unless listed otherwise: 全校重要 when a test marked it tier 0, else
    本系重要. Off-campus keys stay off campus (round two, one layer), as with the real list."""

    def __init__(self, *sites: round1.Site, extra=(), login_checks=()) -> None:
        super().__init__({s.site: s for s in sites}, tuple(extra), tuple(login_checks), "open-plan", ())
        self.tiers: dict[str, int] = {}

    def mark(self, url: str, tier: int) -> None:
        self.tiers.setdefault(scope.site_key(url), tier)

    def site(self, key: str) -> round1.Site | None:
        got = super().site(key)
        if got is not None or not key:
            return got
        if not scope.school_url(f"https://{key}/"):
            return None
        imp = "全校重要" if self.tiers.get(key) == 0 else "本系重要"
        return round1.Site(site=key, round="1", imp=imp, home=f"https://{key}/")


def host_of(url: str) -> str:
    return urlsplit(url).hostname or ""
