"""The conftest guard (no_real_models): in tests/content and tests/retrieval the scoring-model loaders refuse unless a
test is marked models. Both models sit in this machine's cache, so a forgotten fake would load 2.3 GB."""

import pytest

from nknu.core import models


def test_unit_tests_never_load_real_models():
    for load in (models.load_embedder, models.load_reranker, models.shared_embedder, models.shared_reranker):
        with pytest.raises(models.ModelUnavailable, match="unit tests"):
            load()
