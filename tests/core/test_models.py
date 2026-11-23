"""nknu.core.models with fakes: the pins, the device order, offline loading at the pinned revisions, load failures as
ModelUnavailable, the one-per-process shared models, and the lab PC's download. The two tests marked models load the
real models from this machine's cache (deselected by default; skipped when the cache is absent)."""

import hashlib
import json
import os
import subprocess
import sys
import threading
import time
import tomllib
from types import SimpleNamespace

import numpy as np
import pytest

from nknu.core import models
from nknu.settings import REPO_ROOT


def _fake_st(fail: Exception | None = None):
    """A stand-in for the sentence_transformers module: its two classes record how they were built."""
    built = []

    class Model:
        def __init__(self, name, **kwargs):
            if fail is not None:
                raise fail
            self.name, self.kwargs, self.max_seq_length = name, kwargs, 8192
            built.append(self)

    module = SimpleNamespace(SentenceTransformer=type("SentenceTransformer", (Model,), {}),
                             CrossEncoder=type("CrossEncoder", (Model,), {}))
    return module, built


def test_pins():
    assert (models.EMBED_ID, models.EMBED_REVISION) == ("BAAI/bge-m3", "5617a9f61b028005a4858fdac845db406aefb181")
    assert (models.RERANK_ID, models.RERANK_REVISION) == ("BAAI/bge-reranker-v2-m3",
                                                          "953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e")
    assert (models.EMBED_DIM, models.BATCH) == (1024, 16)
    # spec 2026-11-16 §三: both models read 1024 tokens (512 before: what an old vector was cut at)
    assert (models.BASE_SEQ, models.EMBED_MAX_SEQ, models.RERANK_MAX_SEQ) == (512, 1024, 1024)


def test_pipeline_extra_pins():
    extra = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())["project"]["optional-dependencies"]["pipeline"]
    assert sorted(extra) == ["docling==2.145.0", "onnxruntime==1.31.1", "rapidocr==3.10.0",
                             "sentence-transformers==6.2.0", "torch==2.15.0"]


def test_pick_device():
    assert models.pick_device({}, cuda=lambda: True, mps=lambda: True) == "cuda"
    assert models.pick_device({}, cuda=lambda: False, mps=lambda: True) == "mps"
    assert models.pick_device({}, cuda=lambda: False, mps=lambda: False) == "cpu"
    assert models.pick_device({"NKNU_DEVICE": "cpu"}, cuda=lambda: True, mps=lambda: True) == "cpu"


def test_is_cached_false_for_a_model_never_downloaded():
    assert models.is_cached("BAAI/not-a-model", "0" * 40) is False


def test_loader_refuses_without_cache(monkeypatch):
    monkeypatch.setattr(models, "is_cached", lambda *_: False)
    with pytest.raises(models.ModelUnavailable, match="python -m nknu.core.models download"):
        models.load_embedder()


def test_loaders_are_offline_and_pinned(monkeypatch):
    pytest.importorskip("torch")
    st, built = _fake_st()
    asked = []
    monkeypatch.setattr(models, "_st", lambda: st)
    monkeypatch.setattr(models, "is_cached", lambda *args: asked.append(args) or True)
    emb = models.load_embedder("mps")
    model = built[-1]
    assert type(model).__name__ == "SentenceTransformer" and model.name == "BAAI/bge-m3"
    assert model.kwargs["revision"] == models.EMBED_REVISION and model.kwargs["local_files_only"] is True
    assert model.kwargs["device"] == "mps" and str(model.kwargs["model_kwargs"]["dtype"]) == "torch.float16"
    assert model.max_seq_length == 1024 and emb.max_seq == 1024
    assert (emb.model_id, emb.revision, emb.dim, emb.device) == (models.EMBED_ID, models.EMBED_REVISION, 1024, "mps")
    rr = models.load_reranker("cpu")
    model = built[-1]
    assert type(model).__name__ == "CrossEncoder" and model.name == "BAAI/bge-reranker-v2-m3"
    assert model.kwargs["revision"] == models.RERANK_REVISION and model.kwargs["local_files_only"] is True
    assert model.kwargs["max_length"] == 1024 and model.kwargs["device"] == "cpu"
    assert "float16" not in str(model.kwargs.get("model_kwargs"))
    assert (rr.model_id, rr.revision, rr.device) == (models.RERANK_ID, models.RERANK_REVISION, "cpu")
    assert asked == [(models.EMBED_ID, models.EMBED_REVISION), (models.RERANK_ID, models.RERANK_REVISION)]


def test_load_error_is_model_unavailable(monkeypatch):
    st, _ = _fake_st(fail=OSError("weights missing"))
    monkeypatch.setattr(models, "_st", lambda: st)
    monkeypatch.setattr(models, "is_cached", lambda *_: True)  # only config.json is checked: a partial cache passes
    with pytest.raises(models.ModelUnavailable, match="BAAI/bge-m3.*weights missing"):
        models.load_embedder("cpu")
    with pytest.raises(models.ModelUnavailable, match="BAAI/bge-reranker-v2-m3.*weights missing"):
        models.load_reranker("cpu")

    def no_torch():
        raise ModuleNotFoundError("No module named 'torch'")

    monkeypatch.setattr(models, "_st", no_torch)
    with pytest.raises(models.ModelUnavailable, match="BAAI/bge-m3.*torch"):
        models.load_embedder()


def test_offline_flags_are_set_before_huggingface_hub_loads(tmp_path):
    pytest.importorskip("huggingface_hub")
    code = ("import nknu.core.models, os, huggingface_hub.constants as c; "
            "print(c.HF_HUB_OFFLINE, os.environ['DISABLE_SAFETENSORS_CONVERSION'])")
    env = {k: v for k, v in os.environ.items()
           if k not in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "DISABLE_SAFETENSORS_CONVERSION")}

    def fresh(**extra):
        done = subprocess.run([sys.executable, "-c", code], env={**env, **extra}, cwd=tmp_path, capture_output=True,
                              text=True, timeout=120, check=True)
        return done.stdout.strip()

    assert fresh() == "True 1"
    assert fresh(HF_HUB_OFFLINE="0") == "False 1"  # an explicit value wins: the lab PC's download


def test_shared_instances_load_once(monkeypatch):
    monkeypatch.setattr(models, "_SHARED", {})
    calls = []

    def slow_load(device=None):
        calls.append("embedder")
        time.sleep(0.05)
        return object()

    monkeypatch.setattr(models, "load_embedder", slow_load)
    got = []
    threads = [threading.Thread(target=lambda: got.append(models.shared_embedder())) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert calls == ["embedder"] and len(got) == 4 and all(g is got[0] for g in got)
    assert models.shared_embedder() is got[0] and calls == ["embedder"]

    def broken(device=None):
        calls.append("reranker")
        raise models.ModelUnavailable("BAAI/bge-reranker-v2-m3 broken")

    now = [1000.0]
    monkeypatch.setattr(models, "_clock", lambda: now[0])
    monkeypatch.setattr(models, "load_reranker", broken)
    for _ in range(3):  # a failed load is not paid again on every query: keyword ranking for RETRY_AFTER_S
        with pytest.raises(models.ModelUnavailable, match="bge-reranker-v2-m3 broken"):
            models.shared_reranker()
    assert calls == ["embedder", "reranker"]
    now[0] += models.RETRY_AFTER_S - 1
    with pytest.raises(models.ModelUnavailable, match="10 分钟内不再试"):
        models.shared_reranker()
    assert calls == ["embedder", "reranker"]
    # review 11-02: a passing failure (MPS out of memory while a build holds the GPU) must not last until a restart
    now[0] += 1
    with pytest.raises(models.ModelUnavailable):
        models.shared_reranker()  # tried again, failed again: the wait starts over
    assert calls == ["embedder", "reranker", "reranker"]
    now[0] += models.RETRY_AFTER_S
    ok = object()
    monkeypatch.setattr(models, "load_reranker", lambda device=None: calls.append("reranker") or ok)
    assert models.shared_reranker() is ok and models.shared_reranker() is ok
    assert calls == ["embedder", "reranker", "reranker", "reranker"]


def test_encode_and_score_contract():
    class Half:  # the float16 model: vectors not quite unit length, numbers in float16
        def encode(self, texts, **kwargs):
            assert kwargs["batch_size"] == 16
            return np.full((len(texts), 4), 2.0, dtype=np.float16)

        def predict(self, pairs, **kwargs):
            assert kwargs["batch_size"] == 16 and pairs == [("q", "p1"), ("q", "p2")]
            return np.array([0.9, 0.1], dtype=np.float16)

    emb = models.Embedder(Half(), dim=4)
    v = emb.encode(["a", "b"])
    assert v.dtype == np.float32 and v.shape == (2, 4) and np.allclose(np.linalg.norm(v, axis=1), 1)
    assert emb.encode([]).shape == (0, 4)
    rr = models.Reranker(Half())
    s = rr.score("q", ["p1", "p2"])
    assert all(type(x) is float for x in s) and 0 <= s[1] < s[0] <= 1
    assert rr.score("q", []) == []

    class Broken:  # an inference that returns NaN is an error the caller falls back on, never a ranking
        def encode(self, texts, **kwargs):
            return np.full((len(texts), 4), np.nan, dtype=np.float32)

        def predict(self, pairs, **kwargs):
            return np.array([np.nan] * len(pairs))

    with pytest.raises(RuntimeError, match="NaN"):
        models.Embedder(Broken(), dim=4).encode(["a"])
    with pytest.raises(RuntimeError, match="NaN"):
        models.Reranker(Broken()).score("q", ["p"])
    with pytest.raises(RuntimeError, match="1024"):
        models.Embedder(Half()).encode(["a"])  # 4 numbers where bge-m3 gives 1024


def test_calls_on_one_model_run_one_at_a_time():
    state = {"now": 0, "most": 0}
    count = threading.Lock()

    class Slow:
        def _work(self):
            with count:
                state["now"] += 1
                state["most"] = max(state["most"], state["now"])
            time.sleep(0.02)
            with count:
                state["now"] -= 1

        def encode(self, texts, **kwargs):
            self._work()
            return np.ones((len(texts), 4), dtype=np.float32)

        def predict(self, pairs, **kwargs):
            self._work()
            return np.full(len(pairs), 0.5, dtype=np.float32)

    emb, rr = models.Embedder(Slow(), dim=4), models.Reranker(Slow())
    for call in ([lambda: emb.encode(["a"])] * 4, [lambda: rr.score("q", ["p"])] * 4):
        state.update(now=0, most=0)
        threads = [threading.Thread(target=c) for c in call]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert state["most"] == 1


def _fake_hub(cache, weights, corrupt=()):
    asked = []

    def snapshot_download(repo_id, *, revision, ignore_patterns, cache_dir):
        asked.append((repo_id, revision, tuple(ignore_patterns), cache_dir))
        snap = cache / f"models--{repo_id.replace('/', '--')}" / "snapshots" / revision
        snap.mkdir(parents=True, exist_ok=True)
        (snap / "config.json").write_text("{}")
        (snap / "model.safetensors").write_bytes(b"corrupt" if repo_id in corrupt else weights[repo_id])
        return str(snap)

    class HfApi:
        def model_info(self, repo_id, *, revision, files_metadata):
            assert files_metadata is True
            lfs = SimpleNamespace(sha256=hashlib.sha256(weights[repo_id]).hexdigest())
            return SimpleNamespace(siblings=[
                SimpleNamespace(rfilename="config.json", lfs=None),
                SimpleNamespace(rfilename="model.safetensors", lfs=lfs),
                SimpleNamespace(rfilename="onnx/model.onnx", lfs=SimpleNamespace(sha256="0" * 64))])

    return SimpleNamespace(snapshot_download=snapshot_download, HfApi=HfApi), asked


def test_download_uses_the_pins(monkeypatch, tmp_path):
    cache, runs = tmp_path / "hub", []
    weights = {models.EMBED_ID: b"embed weights", models.RERANK_ID: b"rerank weights"}
    hub, asked = _fake_hub(cache, weights)
    monkeypatch.setattr(models, "_hub", lambda: hub)
    monkeypatch.setattr(models, "_run", runs.append)
    out = models.download(cache, docling_dir=tmp_path / "docling")
    assert [a[:2] for a in asked] == [(models.EMBED_ID, models.EMBED_REVISION),
                                      (models.RERANK_ID, models.RERANK_REVISION)]
    assert all("onnx/*" in a[2] and a[3] == str(cache) for a in asked)
    assert out["ok"] is True and [[f["file"] for f in m["files"]] for m in out["models"]] == [["model.safetensors"]] * 2
    manifest = json.loads((tmp_path / "models_manifest.json").read_text(encoding="utf-8"))  # next to the cache
    assert out["path"] == str(tmp_path / "models_manifest.json") and manifest["ok"] is True
    assert [(m["id"], m["revision"]) for m in manifest["models"]] == [(models.EMBED_ID, models.EMBED_REVISION),
                                                                       (models.RERANK_ID, models.RERANK_REVISION)]
    assert runs == [[sys.executable, "-m", "docling.cli.tools", "models", "download", "layout", "tableformer", "-o",
                     str(tmp_path / "docling")]]
    # a weight file whose sha256 differs from the Hub's record is flagged, never trusted
    hub, _ = _fake_hub(cache, weights, corrupt={models.RERANK_ID})
    monkeypatch.setattr(models, "_hub", lambda: hub)
    out = models.download(cache)
    assert out["ok"] is False and [m["ok"] for m in out["models"]] == [True, False] and out["docling"] is None
    assert "sha256" in out["models"][1]["files"][0]["why"]


def test_download_needs_the_network_flag(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(models, "download", lambda *a, **k: pytest.fail("downloaded while offline"))
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    assert models.main(["download"]) == 2
    assert "下载要连网：请用 HF_HUB_OFFLINE=0 python -m nknu.core.models download" in capsys.readouterr().err
    done = []

    def fake_download(cache_dir=None, *, docling_dir=None):
        done.append((cache_dir, docling_dir))
        return {"ok": True, "path": str(tmp_path / "models_manifest.json"), "docling": None,
                "models": [{"id": models.EMBED_ID, "revision": models.EMBED_REVISION, "ok": True,
                            "files": [{"file": "pytorch_model.bin", "ok": True}]}]}

    monkeypatch.setattr(models, "download", fake_download)
    monkeypatch.setenv("HF_HUB_OFFLINE", "0")
    assert models.main(["download", "--cache-dir", str(tmp_path / "hub")]) == 0
    assert done == [(tmp_path / "hub", None)]
    assert "bge-m3（5617a9f…）" in capsys.readouterr().out


def test_check_reports_each_model_and_the_device(monkeypatch, capsys):
    monkeypatch.setattr(models, "is_cached", lambda model_id, revision: True)
    monkeypatch.setattr(models, "_weights_cached", lambda model_id, revision: model_id == models.EMBED_ID)
    monkeypatch.setattr(models, "_missing_packages", list)
    monkeypatch.setenv("NKNU_DEVICE", "cpu")
    assert models.main(["check"]) == 1
    out = capsys.readouterr().out
    assert "bge-m3：已在本机快取（5617a9f…）" in out and "bge-reranker-v2-m3：快取不完整" in out
    assert "装置：cpu" in out
    monkeypatch.setattr(models, "_weights_cached", lambda *_: True)
    assert models.main(["check"]) == 0
    monkeypatch.setattr(models, "is_cached", lambda model_id, revision: model_id == models.EMBED_ID)
    assert models.main(["check"]) == 1
    assert "bge-reranker-v2-m3：没有（953dc6f…）" in capsys.readouterr().out


def _need(model_id, revision):
    pytest.importorskip("sentence_transformers")
    if not models.is_cached(model_id, revision):
        pytest.skip(f"{model_id} 不在本机快取")


@pytest.mark.models
def test_real_embedder():
    _need(models.EMBED_ID, models.EMBED_REVISION)
    v = models.load_embedder().encode(["停修申請", "TOEIC 多益"])
    assert v.shape == (2, 1024) and np.allclose(np.linalg.norm(v, axis=1), 1, atol=1e-3)


@pytest.mark.models
def test_real_reranker():
    _need(models.RERANK_ID, models.RERANK_REVISION)
    s = models.load_reranker().score("停修截止日期", ["115學年度第1學期停修申請至11月16日截止", "學生證遺失補辦"])
    assert 0 <= s[1] < s[0] <= 1
