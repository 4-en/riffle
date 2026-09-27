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


# ---- file and folder names --------------------------------------------------------

from riffle.query import name_matcher, name_matches, name_words  # noqa: E402


@pytest.mark.parametrize(
    "name, words",
    [
        ("IMG_4711", set()),  # camera names carry no words
        ("DSCF0012", set()),
        ("PXL_20230514_101010123", set()),
        ("_MG_1234", set()),
        ("P1010001", set()),
        ("IMG_4cat7", set()),  # letters wedged between digits are a code
        ("beach_sunset-2019", {"beach", "sunset", "2019"}),  # years count
        ("BeachSunsetParis", {"beach", "sunset", "paris"}),  # camelCase
        ("beach2019", {"beach", "2019"}),
        ("Paris 2019", {"paris", "2019"}),
    ],
)
def test_name_words(name, words):
    assert name_words(name) == words


@pytest.mark.parametrize(
    "q, name, hit",
    [
        ("cat", "cat_01", True),
        ("cat", "catalogue", False),  # whole words only
        ("cat", "cats-on-roof", True),  # a plural s either way
        ("cats", "cat", True),
        ("red car", "red_car", True),
        ("red car", "red_bus", False),  # every word must be there
        ("red car", "car-red-2019", True),
        ("boats at sunset", "boat_sunset", True),  # filler words are ignored
        ("beach | lake", "Lake", True),
        ("lake -draft", "lake draft", False),  # an excluded term blocks the match
        ("img", "IMG_4711", False),
        ("4711", "IMG_4711", False),
        ("2019", "beach_2019", True),
    ],
)
def test_name_matching(q, name, hit):
    assert name_matcher(q)(name) is hit


def test_nothing_to_match():
    assert name_matcher("the -people") is None
    assert name_matches("of", [(1, "/photos", "cat.jpg")]) == {}


def test_file_before_folder(tmp_path):
    rows = [(1, "/photos", "trip/cat.jpg"), (2, "/photos", "cat/IMG_1.jpg"), (3, "/photos/cat", "IMG_2.jpg"), (4, "/photos", "dog/IMG_3.jpg")]
    assert name_matches("cat", rows) == {1: "file", 2: "folder", 3: "folder"}  # a root file: its source folder


def test_search_puts_name_matches_first(indexed):
    enc = FakeClip().encode_text
    with TestClient(create_app(indexed, text_encoder=enc)) as c:
        def search(q, **kw):
            return c.get("/api/search/text", params={"q": q, "dupes": "all", **kw}).json()["items"]

        plain = search("edit", names="false")
        assert all("name_match" not in i for i in plain)
        items = search("edit")
        assert items[0]["rel_path"] == "trip/IMG_0002_edit.png" and items[0]["name_match"] == "file"
        assert sorted(i["id"] for i in items) == sorted(i["id"] for i in plain)  # reordered, nothing dropped
        rest = [i for i in items[1:]]
        assert all(i.get("name_match") is None for i in rest)
        folder = search("trip")  # every photo is in "trip": all folder matches, in image order
        assert [i["name_match"] for i in folder] == ["folder"] * len(folder)
        assert [i["id"] for i in folder] == [i["id"] for i in search("trip", names="false")]
