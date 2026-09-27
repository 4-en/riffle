"""Text search with alternatives and excluded terms: "beach | lake -dogs"."""

import numpy as np
import pytest
from fastapi.testclient import TestClient

from riffle.embed import load_embeddings
from riffle.query import NEGATIVE_WEIGHT, parse, query_vectors, score
from riffle.server import create_app
from conftest import FakeClip


@pytest.mark.parametrize(
    "q, expected",
    [
        ("animals -dogs", (["animals"], ["dogs"])),
        ("-dogs animals -cats", (["animals"], ["dogs", "cats"])),
        ('street -"parked cars" at night', (["street at night"], ["parked cars"])),
        ("black-and-white portraits", (["black-and-white portraits"], [])),  # hyphen inside a word
        ("sunset - beach", (["sunset - beach"], [])),  # a lone minus is just text
        ("-people", ([], ["people"])),
        ('-""', ([], [])),
        ("beach at sunset | snowy mountains -people", (["beach at sunset", "snowy mountains"], ["people"])),
        ("beach||lake |", (["beach", "lake"], [])),  # empty alternatives are ignored
        ("street -people|cars", (["street"], ["people", "cars"])),  # either is excluded
    ],
)
def test_parse(q, expected):
    assert parse(q) == expected


def unit(v):
    return v / np.linalg.norm(v)


def test_query_vectors():
    enc = FakeClip().encode_text
    a, b, d = (enc([t])[0] for t in ("animals", "birds", "dogs"))
    assert np.allclose(query_vectors("animals", enc), [a], atol=1e-6)
    assert np.allclose(query_vectors("animals -dogs", enc), [unit(a - NEGATIVE_WEIGHT * d)], atol=1e-6)
    # Each alternative gets the exclusions.
    Q = query_vectors("animals | birds -dogs", enc)
    assert np.allclose(Q, [unit(a - NEGATIVE_WEIGHT * d), unit(b - NEGATIVE_WEIGHT * d)], atol=1e-6)
    assert np.allclose(query_vectors("-dogs", enc), [-d], atol=1e-6)  # least like dogs first
    assert query_vectors('-""', enc) is None


def test_score_is_the_best_alternative():
    E = np.eye(3)
    Q = np.array([[1.0, 0, 0], [0, 1.0, 0]])
    assert score(E, Q).tolist() == [1.0, 1.0, 0.0]  # a match for either alternative counts fully


def ranked_like(c, cfg, enc, q):
    res = c.get("/api/search/text", params={"q": q, "dupes": "all"}).json()
    E, ids = load_embeddings(cfg)
    expected = score(E, query_vectors(q, enc))
    by_id = dict(zip(ids.tolist(), expected.tolist()))
    assert [i["id"] for i in res["items"]] == sorted(by_id, key=lambda i: -by_id[i])
    assert [i["score"] for i in res["items"]] == pytest.approx(sorted(expected.tolist(), reverse=True), abs=1e-3)


def test_search_with_alternatives_and_exclusions(indexed):
    enc = FakeClip().encode_text
    with TestClient(create_app(indexed, text_encoder=enc)) as c:
        ranked_like(c, indexed, enc, "red -blue")
        ranked_like(c, indexed, enc, "red | green -blue")
        assert c.get("/api/search/text", params={"q": '-""'}).status_code == 400
        assert c.get("/api/search/text", params={"q": " | "}).status_code == 400
