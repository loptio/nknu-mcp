"""The two scoring models of hybrid retrieval: bge-m3 (dense vectors) and bge-reranker-v2-m3 (a cross-encoder), loaded
from this machine's Hugging Face cache at pinned revisions and never downloaded on the way. Both only return numbers:
the serving path never calls a text-generating model. Importing this module sets HF_HUB_OFFLINE=1 (an explicit value
wins) before anything imports huggingface_hub, which reads it once, when it loads; torch and sentence-transformers load
only with a model. Any load failure is ModelUnavailable, so callers fall back to keyword ranking.

    python -m nknu.core.models check                     # both models cached? which device? (no network)
    HF_HUB_OFFLINE=0 python -m nknu.core.models download [--cache-dir DIR] [--docling DIR]   # the lab PC; network
"""

# The two environment defaults below must run before every other import of this module.
from __future__ import annotations

import os

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("DISABLE_SAFETENSORS_CONVERSION", "1")

import argparse
import fnmatch
import hashlib
import importlib.util
import json
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from nknu.core.timeutil import iso_now

EMBED_ID, EMBED_REVISION, EMBED_DIM = "BAAI/bge-m3", "5617a9f61b028005a4858fdac845db406aefb181", 1024
RERANK_ID, RERANK_REVISION = "BAAI/bge-reranker-v2-m3", "953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e"
MAX_SEQ, BATCH = 512, 16
PINS = ((EMBED_ID, EMBED_REVISION), (RERANK_ID, RERANK_REVISION))
# bge-m3 ships pytorch_model.bin, the reranker model.safetensors; `check` wants one of them beside config.json
WEIGHT_FILES = ("model.safetensors", "pytorch_model.bin")
IGNORE_PATTERNS = ("onnx/*", "*.onnx", "*.onnx_data", "imgs/*")  # the ONNX exports and README pictures
DOCLING_MODELS = ("layout", "tableformer")
MANIFEST_NAME = "models_manifest.json"
DOWNLOAD_HINT = "在能连网的电脑上执行 HF_HUB_OFFLINE=0 python -m nknu.core.models download"
_TRUE = {"1", "ON", "YES", "TRUE"}  # huggingface_hub's reading of an environment flag


class ModelUnavailable(RuntimeError):
    """A scoring model cannot be used in this process: not in the local cache, a partial cache, torch or
    sentence-transformers missing, or any other load error. The message names the model and the download command."""


def _st():
    import sentence_transformers

    return sentence_transformers


def _torch():
    import torch

    return torch


def _hub():
    import huggingface_hub

    return huggingface_hub


def _run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True)


def _never() -> bool:
    return False


def _name(model_id: str) -> str:
    return model_id.rsplit("/", 1)[-1]


def _short(revision: str) -> str:
    return f"{revision[:7]}…"


def pick_device(env: Mapping[str, str] | None = None, *, cuda: Callable[[], bool] | None = None,
                mps: Callable[[], bool] | None = None) -> str:
    """NKNU_DEVICE when set; else cuda, then mps, then cpu. The probes default to torch's (cpu without torch)."""
    forced = ((os.environ if env is None else env).get("NKNU_DEVICE") or "").strip().lower()
    if forced:
        return forced
    if cuda is None or mps is None:
        try:
            torch = _torch()
        except ImportError:
            torch = None
        cuda = cuda or (torch.cuda.is_available if torch is not None else _never)
        mps = mps or (torch.backends.mps.is_available if torch is not None else _never)
    if cuda():
        return "cuda"
    return "mps" if mps() else "cpu"


def _cached_file(model_id: str, revision: str, filename: str) -> bool:
    try:
        from huggingface_hub import try_to_load_from_cache
    except ImportError:
        return False
    return isinstance(try_to_load_from_cache(model_id, filename, revision=revision), str)


def is_cached(model_id: str, revision: str) -> bool:
    """config.json of that revision sits in the local Hugging Face cache (no network). One file only: a partial cache
    passes here and fails at load, as ModelUnavailable."""
    return _cached_file(model_id, revision, "config.json")


def _weights_cached(model_id: str, revision: str) -> bool:
    return any(_cached_file(model_id, revision, name) for name in WEIGHT_FILES)


def _require_cached(model_id: str, revision: str) -> None:
    if not is_cached(model_id, revision):
        raise ModelUnavailable(f"{model_id}（{_short(revision)}）不在本机快取；{DOWNLOAD_HINT}")


def _load_failed(model_id: str, revision: str, exc: Exception) -> ModelUnavailable:
    return ModelUnavailable(f"{model_id}（{_short(revision)}）载入失败（{type(exc).__name__}: {exc}）；"
                            f"快取不完整就{DOWNLOAD_HINT}；没装 torch 就装 pipeline 这组套件")


def _dtype(torch, device: str) -> dict:
    """Half precision on a GPU (cuda, mps); float32 on cpu, where float16 is slow or unsupported."""
    half = device.split(":", 1)[0] in ("cuda", "mps")
    return {"dtype": torch.float16 if half else torch.float32}


def _finite(values: np.ndarray, model_id: str) -> np.ndarray:
    if not np.isfinite(values).all():
        raise RuntimeError(f"{model_id} 算出了 NaN 或无穷大（推理出错，这次不能用它排序）")
    return values


class Embedder:
    """bge-m3 dense vectors (CLS pooling, then its Normalize layer): float32, `dim` wide, unit length. Calls on one
    instance run one at a time."""

    def __init__(self, model, *, device: str = "cpu", model_id: str = EMBED_ID, revision: str = EMBED_REVISION,
                 dim: int = EMBED_DIM) -> None:
        self.model, self.device = model, device
        self.model_id, self.revision, self.dim = model_id, revision, dim
        self._lock = threading.Lock()

    def encode(self, texts: list[str], *, batch_size: int = BATCH) -> np.ndarray:
        texts = list(texts)
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        with self._lock:
            raw = self.model.encode(texts, batch_size=batch_size, convert_to_numpy=True, show_progress_bar=False)
        vecs = np.asarray(raw, dtype=np.float32).reshape(len(texts), -1)
        if vecs.shape[1] != self.dim:
            raise RuntimeError(f"{self.model_id} 给了 {vecs.shape[1]} 维的向量，应该是 {self.dim} 维")
        norms = np.linalg.norm(_finite(vecs, self.model_id), axis=1, keepdims=True)
        return vecs / np.where(norms > 0, norms, 1.0)  # unit length again in float32 after the float16 model


class Reranker:
    """bge-reranker-v2-m3: how well each passage answers the query, in [0, 1] (the sigmoid over its one logit). Calls
    on one instance run one at a time."""

    def __init__(self, model, *, device: str = "cpu", model_id: str = RERANK_ID,
                 revision: str = RERANK_REVISION) -> None:
        self.model, self.device = model, device
        self.model_id, self.revision = model_id, revision
        self._lock = threading.Lock()

    def score(self, query: str, passages: list[str], *, batch_size: int = BATCH) -> list[float]:
        passages = list(passages)
        if not passages:
            return []
        with self._lock:
            raw = self.model.predict([(query, p) for p in passages], batch_size=batch_size, convert_to_numpy=True,
                                     show_progress_bar=False)
        scores = np.asarray(raw, dtype=np.float32).reshape(-1)
        if len(scores) != len(passages):
            raise RuntimeError(f"{self.model_id} 给了 {len(scores)} 个分数，应该是 {len(passages)} 个")
        return [float(s) for s in _finite(scores, self.model_id)]


def load_embedder(device: str | None = None) -> Embedder:
    """bge-m3 at EMBED_REVISION from local files only, MAX_SEQ tokens; any failure is ModelUnavailable."""
    _require_cached(EMBED_ID, EMBED_REVISION)
    try:
        st, torch = _st(), _torch()
        device = device or pick_device()
        model = st.SentenceTransformer(EMBED_ID, revision=EMBED_REVISION, device=device, local_files_only=True,
                                       model_kwargs=_dtype(torch, device))
        model.max_seq_length = MAX_SEQ
    except Exception as exc:
        raise _load_failed(EMBED_ID, EMBED_REVISION, exc) from exc
    return Embedder(model, device=device)


def load_reranker(device: str | None = None) -> Reranker:
    """bge-reranker-v2-m3 at RERANK_REVISION from local files only, MAX_SEQ tokens per (query, passage) pair; any
    failure is ModelUnavailable."""
    _require_cached(RERANK_ID, RERANK_REVISION)
    try:
        st, torch = _st(), _torch()
        device = device or pick_device()
        model = st.CrossEncoder(RERANK_ID, revision=RERANK_REVISION, device=device, local_files_only=True,
                                max_length=MAX_SEQ, model_kwargs=_dtype(torch, device))
    except Exception as exc:
        raise _load_failed(RERANK_ID, RERANK_REVISION, exc) from exc
    return Reranker(model, device=device)


# A failed shared load is tried again after this long, not on every query (each would pay the failed load again) and
# not only after a restart (a passing failure, MPS out of memory while a build holds the GPU, review 11-02).
RETRY_AFTER_S = 600


@dataclass(frozen=True)
class _Failed:
    error: ModelUnavailable
    at: float  # _clock() when the load failed


_SHARED: dict[str, object] = {}  # kind -> the process's model, or _Failed: its last load attempt failed
_SHARED_LOCK = threading.Lock()
_clock = time.monotonic


def _shared(kind: str, load: Callable[[], object]):
    with _SHARED_LOCK:
        got = _SHARED.get(kind)
        if got is None or (isinstance(got, _Failed) and _clock() - got.at >= RETRY_AFTER_S):
            try:
                _SHARED[kind] = load()
            except ModelUnavailable as exc:
                _SHARED[kind] = _Failed(exc, _clock())
                raise
        got = _SHARED[kind]
    if isinstance(got, _Failed):
        raise ModelUnavailable(f"{got.error}（这个进程载入失败过，{RETRY_AFTER_S // 60} 分钟内不再试；"
                               "重启服务就马上再试）")
    return got


def shared_embedder() -> Embedder:
    """The process's one bge-m3, loaded on first use behind a lock; after a failed load, tried again only once
    RETRY_AFTER_S has passed."""
    return _shared("embedder", load_embedder)


def shared_reranker() -> Reranker:
    """The process's one reranker, loaded on first use behind a lock; after a failed load, tried again only once
    RETRY_AFTER_S has passed."""
    return _shared("reranker", load_reranker)


def _sha256(path: Path) -> str:
    with path.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def _ignored(name: str) -> bool:
    return any(fnmatch.fnmatch(name, pattern) for pattern in IGNORE_PATTERNS)


def _verify(snap: Path, name: str, want: str) -> dict:
    local = snap / name
    if not local.is_file():
        return {"file": name, "ok": False, "why": "没下载到"}
    got = _sha256(local)
    out = {"file": name, "bytes": local.stat().st_size, "sha256": got, "ok": got == want}
    if got != want:
        out["why"] = f"sha256 不符（Hub 记录的是 {want}）"
    return out


def download(cache_dir: Path | None = None, *, docling_dir: Path | None = None) -> dict:
    """The lab PC (network; HF_HUB_OFFLINE=0): both models at their pins into the Hugging Face cache, every LFS file
    checked against the Hub's sha256, and models_manifest.json written next to the cache (its parent folder, where
    `hf cache scan` does not take it for a broken repo). With docling_dir, also Docling's layout and table models.
    Returns the manifest with its path; "ok" is false when a file is missing or differs."""
    hub = _hub()
    root = Path(cache_dir) if cache_dir is not None else None
    entries = []
    for repo, revision in PINS:
        snap = Path(hub.snapshot_download(repo, revision=revision, ignore_patterns=list(IGNORE_PATTERNS),
                                          cache_dir=None if cache_dir is None else str(cache_dir)))
        root = root or snap.parents[2]  # <cache>/models--org--name/snapshots/<revision>
        info = hub.HfApi().model_info(repo, revision=revision, files_metadata=True)
        files = [_verify(snap, s.rfilename, s.lfs.sha256) for s in info.siblings or ()
                 if s.lfs is not None and not _ignored(s.rfilename)]
        entries.append({"id": repo, "revision": revision, "path": str(snap), "files": files,
                        "ok": bool(files) and all(f["ok"] for f in files)})
    docling = None
    if docling_dir is not None:
        docling = {"dir": str(docling_dir), "models": list(DOCLING_MODELS), "ok": True}
        try:
            _run([sys.executable, "-m", "docling.cli.tools", "models", "download", *DOCLING_MODELS,
                  "-o", str(docling_dir)])
        except (OSError, subprocess.CalledProcessError) as exc:
            docling.update(ok=False, error=f"{type(exc).__name__}: {exc}")
    manifest = {"written_at": iso_now(), "cache_dir": str(root), "models": entries, "docling": docling,
                "ok": all(e["ok"] for e in entries) and (docling is None or docling["ok"])}
    path = root.parent / MANIFEST_NAME
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    return {**manifest, "path": str(path)}


def _offline(env: Mapping[str, str] | None = None) -> bool:
    env = os.environ if env is None else env
    return (env.get("HF_HUB_OFFLINE") or env.get("TRANSFORMERS_OFFLINE") or "").upper() in _TRUE


def _missing_packages() -> list[str]:
    return [name for name, module in (("torch", "torch"), ("sentence-transformers", "sentence_transformers"))
            if importlib.util.find_spec(module) is None]


def _check() -> int:
    cached = True
    for model_id, revision in PINS:
        if not is_cached(model_id, revision):
            state, cached = "没有", False
        elif not _weights_cached(model_id, revision):
            state, cached = "快取不完整（没有权重档）", False
        else:
            state = "已在本机快取"
        print(f"{_name(model_id)}：{state}（{_short(revision)}）")
    missing = _missing_packages()
    if missing:
        print(f"套件：没装 {'、'.join(missing)}（pipeline 这组套件）")
    forced = (os.environ.get("NKNU_DEVICE") or "").strip()
    print(f"装置：{pick_device()}{'（NKNU_DEVICE 指定）' if forced else ''}")
    if not cached:
        print(f"缺模型：{DOWNLOAD_HINT}")
    if missing:
        print("缺套件：uv sync --extra pipeline --inexact")
    return 0 if cached and not missing else 1


def _report(out: dict) -> int:
    for m in out["models"]:
        bad = "；".join(f"{f['file']} {f.get('why', '')}" for f in m["files"] if not f["ok"])
        state = "sha256 都对" if m["ok"] else bad or "没有可核对的档"
        print(f"{_name(m['id'])}（{_short(m['revision'])}）：{len(m['files'])} 个档，{state}")
    if out["docling"] is not None:
        d = out["docling"]
        print(f"Docling 模型：已下载到 {d['dir']}" if d["ok"] else f"Docling 模型下载失败：{d['error']}")
    print(f"清单：{out['path']}")
    return 0 if out["ok"] else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m nknu.core.models",
                                 description="检索用的两个打分模型（bge-m3、bge-reranker-v2-m3）：看本机快取，或下载")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check", help="两个模型在不在本机快取、会用哪个装置（不连网）")
    dl = sub.add_parser("download", help="按固定版本下载两个模型并核对 sha256（要连网：HF_HUB_OFFLINE=0）")
    dl.add_argument("--cache-dir", type=Path, default=None, help="Hugging Face 快取（默认 ~/.cache/huggingface/hub）")
    dl.add_argument("--docling", type=Path, default=None, metavar="DIR", help="也把 Docling 的版面和表格模型下载到 DIR")
    args = ap.parse_args(argv)
    if args.cmd == "check":
        return _check()
    if _offline():
        print("下载要连网：请用 HF_HUB_OFFLINE=0 python -m nknu.core.models download", file=sys.stderr)
        return 2
    return _report(download(args.cache_dir, docling_dir=args.docling))


if __name__ == "__main__":
    raise SystemExit(main())
