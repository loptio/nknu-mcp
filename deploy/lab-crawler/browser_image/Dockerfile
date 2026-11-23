# nknu-browser (2026-11-17, spec docs/superpowers/specs/2026-11-17-jsfetch-design.md §5 ①; owner 11-17 「可以，你来装」):
# the image nknu-pipeline plus Playwright 1.64.1 (pyproject's extra browser, from uv.lock) and Google Chrome stable from
# Google's own signed apt repository, for `python -m nknu.archive.jsfetch` (jscheck's PlaywrightBrowser starts the
# machine's Chrome, channel chrome, so no Playwright browser download). nknu-pipeline itself is not changed: the
# to-text lanes, the builds and the crawler keep using it.
#
# Build context: C:\nknu-transfer\browser\ (this file only). Build:
#   docker build -t nknu-browser C:\nknu-transfer\browser
# Run Chrome with room in /dev/shm (Docker's default 64 MB makes Chrome crash on big pages): docker run --shm-size 1g …
# Playwright starts Chrome with --no-sandbox by default (chromiumSandbox false), which a root container needs.

FROM nknu-pipeline

ARG DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl gnupg \
 && curl -fsSL https://dl.google.com/linux/linux_signing_key.pub | gpg --dearmor -o /usr/share/keyrings/google-linux.gpg \
 && echo "deb [arch=amd64 signed-by=/usr/share/keyrings/google-linux.gpg] https://dl.google.com/linux/chrome/deb/ stable main" \
      > /etc/apt/sources.list.d/google-chrome.list \
 && apt-get update \
 && apt-get install -y --no-install-recommends google-chrome-stable \
 && rm -rf /var/lib/apt/lists/*

# the same lock and venv as nknu-pipeline, with the browser extra added (uv sync keeps the pipeline extra and the dev
# group, so pytest is there for the tests)
WORKDIR /opt/nknu-lock
RUN --mount=type=cache,target=/root/.cache/uv \
    export UV_HTTP_TIMEOUT=300 UV_HTTP_RETRIES=10 \
 && uv sync --frozen --extra pipeline --extra browser --no-install-project --python 3.13.7

# fail the build, not the first run, when Chrome or Playwright is missing
RUN google-chrome --version \
 && python -c "import importlib.metadata as m, playwright.async_api; print('playwright', m.version('playwright'))"
