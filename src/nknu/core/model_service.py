"""One copy of the two scoring models for every service on the machine (2026-11-21).

The resident services (檢視台, the inspect MCP on 8766, the public service on 8770) each loaded bge-m3 and the
reranker into the graphics card, about 2.2 GB a copy (7.1 GB used that night). The owner: 「可以只加载在一个容器里面
吗？实现现在同样的功能」. So one process holds them and the others ask it for the numbers:

    python -m nknu.core.model_service --host 0.0.0.0 --port 8780     # loads both, then answers

A process with NKNU_MODELS_URL set (http://models:8780) gets a RemoteEmbedder and a RemoteReranker from
nknu.core.models.shared_embedder / shared_reranker instead of loading its own. They have the same methods and
attributes, and what comes back is the float32 the service's Embedder.encode and Reranker.score gave, sent as bytes:
the same numbers a copy in the process would give (the same model, half precision on the same card, the same batches;
nothing is rounded on the way). Both sides send the pinned revision; a service with other models refuses.

When the service cannot answer (stopped, restarting, too slow), the call raises ModelUnavailable and the search ranks
without that model, as it does when a model is missing (hybrid.py) — unless NKNU_MODELS_FALLBACK=local: then the
process loads a copy of its own at that moment and answers with it (the public service: students' searches must not
stop with the internal tools, the 接口 session's condition of 11-21). The rules and the waits are 【Claude】.
"""

from __future__ import annotations

import argparse
import base64
import json
import logging
import os
import sys
import threading
import urllib.error
import urllib.request
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np

from nknu.core import models

log = logging.getLogger(__name__)

URL_ENV = "NKNU_MODELS_URL"
FALLBACK_ENV = "NKNU_MODELS_FALLBACK"
DEFAULT_PORT = 8780
EMBED_WAIT = 30.0    # seconds a query's vector may take, the wait in a queue included
RERANK_WAIT = 120.0  # seconds a rerank may take: tens of passages of up to 1,024 tokens, behind others in the queue
BODY_MAX = 64 * 1024 * 1024
HOSTS = ("models", "127.0.0.1", "localhost", "host.docker.internal")  # the names it answers under (none other)


def _f32(values) -> str:
    return base64.b64encode(np.ascontiguousarray(values, dtype=np.float32).tobytes()).decode("ascii")


def _from_f32(text: str) -> np.ndarray:
    return np.frombuffer(base64.b64decode(text), dtype=np.float32)


def _post(url: str, body: dict, timeout: float) -> dict:
    """One call; any failure (no answer, a refusal, an answer that is no JSON) is ModelUnavailable."""
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST", headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        said = exc.read(500).decode("utf-8", "replace")
        raise models.ModelUnavailable(f"模型服务回 {exc.code}：{said}") from exc
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise models.ModelUnavailable(f"模型服务（{url}）没有回应：{type(exc).__name__}: {exc}") from exc


class _Remote:
    """What the two remote models share: the service's address, and the process's own copy to fall back on."""

    def __init__(self, url: str, kind: str, fallback: Callable[[], object] | None) -> None:
        self.url, self.kind, self.fallback = url.rstrip("/"), kind, fallback
        self.device = "remote"
        self._warned = threading.Event()

    def _fall(self, exc: models.ModelUnavailable):
        if self.fallback is None:
            raise exc
        if not self._warned.is_set():
            self._warned.set()
            log.warning("模型服务不能用，这个进程改用自己载入的一份%s：%s", self.kind, exc)
        return self.fallback()


class RemoteEmbedder(_Remote):
    """models.Embedder's encode, computed by the model service."""

    def __init__(self, url: str, fallback: Callable[[], models.Embedder] | None = None) -> None:
        super().__init__(url, "向量模型", fallback)
        self.model_id, self.revision = models.EMBED_ID, models.EMBED_REVISION
        self.dim, self.max_seq = models.EMBED_DIM, models.EMBED_MAX_SEQ

    def encode(self, texts: list[str], *, batch_size: int = models.BATCH) -> np.ndarray:
        texts = list(texts)
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        try:
            got = _post(self.url + "/embed", {"revision": self.revision, "texts": texts, "batch_size": batch_size},
                        EMBED_WAIT)
            vecs = _from_f32(got["f32"]).reshape(len(texts), self.dim)
        except models.ModelUnavailable as exc:
            return self._fall(exc).encode(texts, batch_size=batch_size)
        except (KeyError, ValueError, TypeError) as exc:
            return self._fall(models.ModelUnavailable(f"模型服务回的向量读不懂：{exc}")).encode(
                texts, batch_size=batch_size)
        return vecs.copy()


class RemoteReranker(_Remote):
    """models.Reranker's score, computed by the model service."""

    def __init__(self, url: str, fallback: Callable[[], models.Reranker] | None = None) -> None:
        super().__init__(url, "重排模型", fallback)
        self.model_id, self.revision = models.RERANK_ID, models.RERANK_REVISION

    def score(self, query: str, passages: list[str], *, batch_size: int = models.BATCH) -> list[float]:
        passages = list(passages)
        if not passages:
            return []
        try:
            got = _post(self.url + "/rerank", {"revision": self.revision, "query": query, "passages": passages,
                                               "batch_size": batch_size}, RERANK_WAIT)
            scores = _from_f32(got["f32"])
            if len(scores) != len(passages):
                raise ValueError(f"{len(scores)} 个分数，应该是 {len(passages)} 个")
        except models.ModelUnavailable as exc:
            return self._fall(exc).score(query, passages, batch_size=batch_size)
        except (KeyError, ValueError, TypeError) as exc:
            return self._fall(models.ModelUnavailable(f"模型服务回的分数读不懂：{exc}")).score(
                query, passages, batch_size=batch_size)
        return [float(s) for s in scores]


def remote_url(env=None) -> str:
    return ((env if env is not None else os.environ).get(URL_ENV) or "").strip()


def remote_embedder(url: str, env=None) -> RemoteEmbedder:
    local = (env if env is not None else os.environ).get(FALLBACK_ENV, "").strip() == "local"
    return RemoteEmbedder(url, (lambda: models._shared("embedder_local", models.load_embedder)) if local else None)


def remote_reranker(url: str, env=None) -> RemoteReranker:
    local = (env if env is not None else os.environ).get(FALLBACK_ENV, "").strip() == "local"
    return RemoteReranker(url, (lambda: models._shared("reranker_local", models.load_reranker)) if local else None)


# ------------------------------------------------------------------------------------------------ the service


def make_handler(embedder, reranker) -> type[BaseHTTPRequestHandler]:
    """The service's requests: GET /health; POST /embed {revision, texts, batch_size} -> {shape, f32}; POST /rerank
    {revision, query, passages, batch_size} -> {f32}. Only under HOSTS (a page elsewhere that reaches the port under a
    name of its own gets nothing)."""

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):  # a line a call would flood the container's log
            pass

        def _send(self, code: int, payload: dict) -> None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _ours(self) -> bool:
            host = (self.headers.get("Host") or "").rsplit(":", 1)[0].lower()
            if host in HOSTS:
                return True
            self._send(421, {"error": "misdirected"})
            return False

        def do_GET(self):
            if not self._ours():
                return
            if self.path != "/health":
                self._send(404, {"error": "not found"})
                return
            self._send(200, {"status": "ok",
                             "embedder": {"model": embedder.model_id, "revision": embedder.revision,
                                          "device": embedder.device, "dim": embedder.dim, "max_seq": embedder.max_seq},
                             "reranker": {"model": reranker.model_id, "revision": reranker.revision,
                                          "device": reranker.device}})

        def do_POST(self):
            if not self._ours():
                return
            size = int(self.headers.get("Content-Length") or 0)
            if not 0 < size <= BODY_MAX:
                self._send(413, {"error": "body size"})
                return
            try:
                body = json.loads(self.rfile.read(size))
                batch = int(body.get("batch_size") or models.BATCH)
                if self.path == "/embed":
                    if body.get("revision") != embedder.revision:
                        self._send(409, {"error": f"这里的向量模型是 {embedder.revision}"})
                        return
                    texts = [str(t) for t in body["texts"]]
                    vecs = embedder.encode(texts, batch_size=batch)
                    self._send(200, {"shape": list(vecs.shape), "f32": _f32(vecs)})
                elif self.path == "/rerank":
                    if body.get("revision") != reranker.revision:
                        self._send(409, {"error": f"这里的重排模型是 {reranker.revision}"})
                        return
                    scores = reranker.score(str(body["query"]), [str(p) for p in body["passages"]], batch_size=batch)
                    self._send(200, {"f32": _f32(scores)})
                else:
                    self._send(404, {"error": "not found"})
            except (KeyError, TypeError, ValueError) as exc:
                self._send(400, {"error": f"{type(exc).__name__}: {exc}"})
            except Exception as exc:  # the model failed on this input: the caller ranks without it
                log.exception("模型服务：这一次没算成")
                self._send(500, {"error": f"{type(exc).__name__}: {exc}"})

    return Handler


def serve(host: str, port: int, *, embedder=None, reranker=None) -> ThreadingHTTPServer:
    """The server, both models loaded first (a call never waits for a load); not yet serving."""
    embedder = embedder or models.load_embedder()
    reranker = reranker or models.load_reranker()
    srv = ThreadingHTTPServer((host, port), make_handler(embedder, reranker))
    srv.daemon_threads = True
    return srv


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m nknu.core.model_service",
                                 description="两个打分模型只载一份，给这台电脑上的服务算向量和重排分数")
    ap.add_argument("--host", default="127.0.0.1", help="在哪个位址上听（容器里用 0.0.0.0，由 Docker 只从本机转进来）")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"端口（默认 {DEFAULT_PORT}）")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    try:
        srv = serve(args.host, args.port)
    except models.ModelUnavailable as exc:
        print(f"模型载不起来：{exc}", file=sys.stderr)
        return 2
    log.info("模型服务开在 %s:%d", args.host, args.port)
    srv.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
