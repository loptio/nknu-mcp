"""One copy of the two scoring models for the machine (nknu.core.model_service, 2026-11-21; the owner: 「可以只加载在一个
容器里面吗？实现现在同样的功能」). What these tests keep: a remote model gives exactly the numbers the same model gives
in the process, it refuses a service with other models, it answers under the machine's own names only, and when the
service cannot answer the process either ranks without the model or, asked to, falls back on a copy of its own.
Made-up models: no torch, no files."""

from __future__ import annotations

import http.client
import threading

import numpy as np
import pytest

from nknu.core import model_service, models


class FakeST:
    """bge-m3's encode as sentence-transformers calls it: a float32 vector a text, from the text's bytes."""

    def encode(self, texts, batch_size, convert_to_numpy, show_progress_bar):
        out = np.zeros((len(texts), models.EMBED_DIM), dtype=np.float32)
        for i, t in enumerate(texts):
            rng = np.random.default_rng(sum(t.encode("utf-8")) + len(t))
            out[i] = rng.standard_normal(models.EMBED_DIM).astype(np.float32) / 7
        return out


class FakeCE:
    """The reranker's predict: a score a pair, in (0, 1), with digits float32 keeps and a decimal print would not."""

    def predict(self, pairs, batch_size, convert_to_numpy, show_progress_bar):
        return np.array([1 / (3 + len(q) + len(p) / 7) for q, p in pairs], dtype=np.float32)


@pytest.fixture
def local():
    return models.Embedder(FakeST()), models.Reranker(FakeCE())


@pytest.fixture
def service(local):
    srv = model_service.serve("127.0.0.1", 0, embedder=local[0], reranker=local[1])
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()
    srv.server_close()


TEXTS = ["停修申請", "校園地圖在哪裡", "", "a" * 3000, "研究生 學位考試 申請 期限"]


def test_the_service_gives_exactly_the_numbers_the_same_models_give_in_the_process(service, local):
    emb, rer = model_service.RemoteEmbedder(service), model_service.RemoteReranker(service)
    got, want = emb.encode(TEXTS), local[0].encode(TEXTS)
    assert got.dtype == np.float32 and got.shape == (len(TEXTS), models.EMBED_DIM)
    assert np.array_equal(got, want)  # bit for bit, not close
    assert rer.score("停修", TEXTS) == local[1].score("停修", TEXTS)
    assert emb.encode([]).shape == (0, models.EMBED_DIM) and rer.score("x", []) == []
    # what the search reads off a model (hybrid.py checks the index against these): the pinned ones
    assert (emb.model_id, emb.revision, emb.dim, emb.max_seq) == (models.EMBED_ID, models.EMBED_REVISION,
                                                                  models.EMBED_DIM, models.EMBED_MAX_SEQ)
    assert (rer.model_id, rer.revision) == (models.RERANK_ID, models.RERANK_REVISION)


def test_a_service_with_other_models_is_refused_and_a_stopped_one_is_a_missing_model(service, monkeypatch):
    other = model_service.RemoteEmbedder(service)
    other.revision = "0" * 40
    with pytest.raises(models.ModelUnavailable, match="409"):
        other.encode(["x"])
    gone = model_service.RemoteReranker("http://127.0.0.1:9")  # nothing listens
    with pytest.raises(models.ModelUnavailable, match="没有回应"):
        gone.score("x", ["y"])


def test_asked_to_the_process_falls_back_on_a_copy_of_its_own(local):
    loads = []
    emb = model_service.RemoteEmbedder("http://127.0.0.1:9", fallback=lambda: loads.append(1) or local[0])
    rer = model_service.RemoteReranker("http://127.0.0.1:9", fallback=lambda: local[1])
    assert np.array_equal(emb.encode(TEXTS[:2]), local[0].encode(TEXTS[:2]))
    assert rer.score("q", TEXTS[:2]) == local[1].score("q", TEXTS[:2])
    env = {model_service.FALLBACK_ENV: "local"}
    assert model_service.remote_embedder("http://x", env).fallback is not None
    assert model_service.remote_reranker("http://x", {}).fallback is None  # not asked: rank without it


def test_it_answers_under_the_machines_own_names_only(service):
    port = int(service.rsplit(":", 1)[1])
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request("GET", "/health", headers={"Host": f"evil.example:{port}"})
    assert conn.getresponse().status == 421
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request("GET", "/health", headers={"Host": f"models:{port}"})
    assert conn.getresponse().status == 200


def test_the_process_asks_the_service_only_when_its_address_is_set(service, monkeypatch, local):
    monkeypatch.setattr(models, "_SHARED", {})
    monkeypatch.setattr(models, "load_embedder", lambda: local[0])
    monkeypatch.setattr(models, "load_reranker", lambda: local[1])
    monkeypatch.delenv(model_service.URL_ENV, raising=False)
    assert models.shared_embedder() is local[0] and models.shared_reranker() is local[1]
    monkeypatch.setattr(models, "_SHARED", {})
    monkeypatch.setenv(model_service.URL_ENV, service)
    emb, rer = models.shared_embedder(), models.shared_reranker()
    assert isinstance(emb, model_service.RemoteEmbedder) and isinstance(rer, model_service.RemoteReranker)
    assert np.array_equal(emb.encode(["停修"]), local[0].encode(["停修"]))
