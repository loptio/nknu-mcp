# The image nknu-pipeline for the lab PC (lab_pc kit, 11-03): Windows 11 + Docker Desktop (WSL 2), one NVIDIA GPU.
# It holds only what does not change while the job runs: Python 3.13 (uv-managed, /opt/uv-python), the packages of
# pyproject.toml + uv.lock with the "pipeline" extra (venv /opt/venv), LibreOffice and bsdtar. The code, the export,
# the to-text cache and the models live in the volumes nknu-work (/work, the repo's layout) and nknu-models (/models),
# so a code change never needs a rebuild.
#
# Build context: C:\nknu-transfer\build\ (this file, compose.yaml, pyproject.toml, uv.lock). Build:
#   docker compose -f C:\nknu-transfer\build\compose.yaml build
# Counted on the Mac 11-03 from uv.lock (the exact wheel files uv picked for linux x86_64, CPython 3.13), the wheels'
# zip directories, the Ubuntu 24.04 package indexes and the registries: about 3.6 GB to download (3.29 GB of wheels,
# 2.98 GB of them torch 2.15.0+cu132 with triton and its CUDA 13.2 libraries; 0.21 GB for 166 Ubuntu packages; Python
# 34 MB; uv 19 MB; ubuntu:24.04 30 MB) and an image of about 7.3 GB (6.28 GB of unpacked wheels, 0.57 GB of Ubuntu
# packages, Python 98 MB, the base 78 MB; the bytecode, about 0.25 GB, is a guess). The uv cache below keeps another
# ~6 GB on the lab PC after the build (docker builder prune frees it).
# Every locked package has a manylinux wheel for CPython 3.13 on x86_64 (uv sync --dry-run --no-build for that
# platform passed): nothing compiles, so no compiler is installed. uv sync also installs the default dev group
# (pytest, ruff, ...), so the tests can run in the container.
#
# No CUDA base image: torch's wheels bring the CUDA 13.2 runtime, cuDNN 9.26 and the other CUDA 13 libraries (cuBLAS
# and nvJitLink locked at 13.4), and Docker Desktop hands the container the Windows NVIDIA driver through WSL 2
# (libcuda.so). That Windows driver must be 580 or newer (CUDA 13.x minor version compatibility); 595 or newer is the
# branch that goes with CUDA 13.2 (NVIDIA's CUDA release notes). Never install a Linux NVIDIA driver in WSL.

FROM ghcr.io/astral-sh/uv:0.13.5@sha256:32a7bb39b496babbd997b0a39a4d66c126bdaf5661b302bf6f06abda8deca6ff AS uv

FROM ubuntu:24.04

# libreoffice-writer/-calc/-impress: doc, odt, rtf / xls, ods / ppt, odp to docx, xlsx, pptx (nknu.totext.libreoffice;
#   Ubuntu 24.04 ships LibreOffice 24.2, the Mac has 26.2).
# libarchive-tools (universe, enabled in ubuntu:24.04): bsdtar, which lists and extracts RAR (nknu.totext.archives).
# fonts-noto-cjk: Chinese glyphs for PDF pages whose fonts are not embedded, when pdfium draws the page for Docling's
#   layout model and for OCR (the Mac draws them with its own Chinese fonts).
# tzdata: TZ=Asia/Taipei below, so log times read as on the Mac. ca-certificates: HTTPS for the models-download service.
# The Python wheels need nothing more: every ELF file of the non-GPU wheels links only glibc, libstdc++, libgcc_s and
# zlib (all in ubuntu:24.04; opencv is the headless build, so no libGL or glib); torch bundles libgomp and libgfortran.
ARG DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      ca-certificates \
      fonts-noto-cjk \
      libarchive-tools \
      libreoffice-calc \
      libreoffice-impress \
      libreoffice-writer \
      tzdata \
 && rm -rf /var/lib/apt/lists/*

COPY --from=uv /uv /uvx /usr/local/bin/

# Python 3.13.7, the Mac's version, managed by uv; the venv in /opt/venv; the bytecode compiled once here. The uv cache
# is a BuildKit cache mount: a build that fails half-way (a network drop) keeps the wheels it already downloaded, and
# running the same build command again carries on. Long timeouts and more retries for a slow or flaky network.
ENV UV_PYTHON_INSTALL_DIR=/opt/uv-python \
    UV_MANAGED_PYTHON=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy
WORKDIR /opt/nknu-lock
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    export UV_HTTP_TIMEOUT=300 UV_HTTP_RETRIES=10 \
 && uv python install 3.13.7 --no-bin \
 && uv sync --frozen --extra pipeline --no-install-project --python 3.13.7

ENV LANG=C.UTF-8 \
    PYTHONUTF8=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/work/src \
    HF_HOME=/models/huggingface \
    HF_HUB_CACHE=/models/huggingface/hub \
    HF_HUB_OFFLINE=1 \
    NKNU_DOCLING_ARTIFACTS=/models/docling/models \
    NVIDIA_DRIVER_CAPABILITIES=compute,utility \
    TZ=Asia/Taipei \
    VIRTUAL_ENV=/opt/venv \
    PATH=/opt/venv/bin:$PATH

# Fail the build, not the first lane, when a library is missing: every native module the pipeline uses imports here
# (torch imports without a GPU), and both external programs start. Any cache the imports make goes to /tmp, not /models.
RUN HF_HOME=/tmp/hf-build-check HF_HUB_CACHE=/tmp/hf-build-check/hub python -c "import cv2, \
docling.document_converter, docling_parse.pdf_parsers, onnxruntime, opencc, pypdfium2, rapidocr, \
sentence_transformers, torch, torchvision; \
print('python', __import__('platform').python_version(), '| torch', torch.__version__, 'CUDA', torch.version.cuda, \
'| opencv', cv2.__version__, '| onnxruntime', onnxruntime.__version__)" \
 && soffice -env:UserInstallation=file:///tmp/lo-build-check --headless --version \
 && bsdtar --version \
 && rm -rf /tmp/hf-build-check /tmp/lo-build-check

WORKDIR /work
