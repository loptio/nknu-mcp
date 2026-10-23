import json
from collections import Counter
from pathlib import Path

import yaml

from tests.search.evaluate import evaluate, evaluate_found, gold_exists

HERE = Path(__file__).parent


def load(name: str) -> list[dict]:
    return yaml.safe_load((HERE / name).read_text())


def read(name: str) -> str:
    return (HERE / name).read_text()


def test_recall_floors_and_no_regression(world):
    m = evaluate(world, load("queries.yaml"))
    b = json.loads(read("baseline.json"))
    assert m["r1"] >= 0.80 and m["r5"] >= 0.95, m
    assert m["r1"] >= b["r1"] and m["r5"] >= b["r5"] and m["noans_acc"] >= b["noans_acc"], (m, b)


def test_thresholds_on_calibration(world):
    assert evaluate_found(world, load("calibration.yaml")) >= 0.90


def test_query_sets_shape():
    queries, calibration = load("queries.yaml"), load("calibration.yaml")
    assert len(queries) == 80 and Counter(q["type"] for q in queries)["en"] == 10 and len(calibration) == 20
    assert not {q["q"] for q in queries} & {q["q"] for q in calibration}
    assert len({q["q"] for q in queries}) == 80
    for q in queries + calibration:
        assert (q["type"] == "noans") == (not q["gold"]), q
        assert all(len(g) == 1 and next(iter(g)) in ("card", "calendar", "doc") for g in q["gold"]), q


def test_every_gold_exists(world):
    missing = [(q["q"], g) for name in ("queries.yaml", "calibration.yaml") for q in load(name) for g in q["gold"]
               if not gold_exists(world, g)]
    assert not missing
