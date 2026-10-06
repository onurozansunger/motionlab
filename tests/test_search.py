import numpy as np
import pytest

from motionlab.search import intervals, parse_query, search_index
from motionlab.vision import dominant_color


@pytest.mark.parametrize("query", ["BLUE CAR", "blue cars", "mavi araba", "mavi arabalar"])
def test_multilingual_object_filters(query):
    assert parse_query(query) == ({2}, {"blue"}, True)


def test_scene_description_is_not_misrepresented_as_object_match():
    assert parse_query("a blue car beside a building")[2] is False
    assert parse_query("a box being left on a table")[2] is False


def test_other_supported_classes_use_grounded_search():
    assert parse_query("red elephant") == ({20}, {"red"}, True)


def test_missing_observations_split_intervals():
    observations = [{"time": value} for value in [0.2, 0, 0.4, 2.4, 2.6]]
    assert [len(group) for group in intervals(observations)] == [3, 2]


def make_index():
    return {
        "duration": 8,
        "tracks": [
            {
                "id": 1,
                "class_id": 2,
                "label": "car",
                "color": "blue",
                "thumbnail": "track-1.jpg",
                "observations": [
                    {"time": t, "confidence": 0.9, "box": [1, 1, 8, 8]}
                    for t in [0, 0.2, 0.4, 4, 4.2]
                ],
            }
        ],
        "samples": [
            {"time": t, "thumbnail": f"scene-{i}.jpg"}
            for i, t in enumerate([0, 1, 2, 3, 4, 5, 6, 7])
        ],
    }


def test_object_results_have_actual_boxes_and_do_not_bridge_gaps():
    found = search_index(make_index(), np.empty((0, 2)), "blue car", None)
    assert found["mode"] == "object"
    assert len(found["results"]) == 2
    assert found["results"][0]["end"] == pytest.approx(0.6)
    assert found["results"][0]["box"] == [1, 1, 8, 8]
    assert found["results"][1]["start"] == 4


def test_absent_object_and_time_filter():
    assert search_index(make_index(), np.empty((0, 2)), "red car", None)["results"] == []
    result = search_index(make_index(), np.empty((0, 2)), "blue car", None, start=3, end=5)
    assert len(result["results"]) == 1
    assert result["results"][0]["start"] == 4


def test_scene_results_are_temporally_diverse_and_not_grounded():
    class Encoder:
        def encode(self, **kwargs):
            return np.array([[1.0, 0.0]])

    vectors = np.array([[i / 10, 1] for i in range(8)])
    result = search_index(make_index(), vectors, "a highway at dusk", Encoder())
    assert result["mode"] == "semantic"
    assert result["results"][0]["seek"] == 7
    assert all(item["track_id"] is None and item["box"] is None for item in result["results"])
    times = [item["seek"] for item in result["results"]]
    assert all(abs(a - b) >= 2.5 for i, a in enumerate(times) for b in times[i + 1 :])


@pytest.mark.parametrize(
    "bgr, expected",
    [((0, 0, 255), "red"), ((255, 0, 0), "blue"), ((255, 255, 255), "white"), ((0, 0, 0), "black")],
)
def test_color_estimator(bgr, expected):
    image = np.full((30, 30, 3), bgr, dtype=np.uint8)
    assert dominant_color(image, [0, 0, 30, 30]) == expected


def test_color_with_window_like_region():
    image = np.full((100, 100, 3), 230, dtype=np.uint8)
    image[45:75, 20:80] = (90, 50, 20)
    assert dominant_color(image, [0, 0, 100, 100]) == "white"
