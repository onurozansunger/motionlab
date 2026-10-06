import json

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from motionlab.api import create_app
from motionlab.workspace import Board, object_vectors, render_board, visual_matches


class Encoder:
    calls = 0

    def encode(self, *, images=None, text=None):
        self.calls += 1
        if text is not None:
            return np.array([[1.0, 0.0, 0.0]])
        vectors = np.array([np.mean(image, axis=(0, 1)) for image in images], dtype=np.float32)
        return vectors / np.maximum(np.linalg.norm(vectors, axis=1, keepdims=True), 1e-9)


@pytest.fixture
def workspace(tmp_path):
    app = create_app(tmp_path)
    store = app.state.store
    encoder = Encoder()
    store.models = lambda: (None, encoder)
    jobs = []
    for name in ["First video", "Second video"]:
        job_id = store.create(name)
        directory = store.runs / job_id
        writer = cv2.VideoWriter(
            str(directory / "video.mp4"), cv2.VideoWriter_fourcc(*"mp4v"), 25, (160, 96)
        )
        for _ in range(20):
            frame = np.zeros((96, 160, 3), dtype=np.uint8)
            frame[10:60, 10:60] = (0, 0, 255)
            frame[10:60, 90:140] = (255, 0, 0)
            writer.write(frame)
        writer.release()
        tracks = []
        for track_id, color, box in [(1, "red", [10, 10, 60, 60]), (2, "blue", [90, 10, 140, 60])]:
            tracks.append(
                {
                    "id": track_id,
                    "class_id": 2,
                    "color": color,
                    "label": "car",
                    "thumbnail": f"track-{track_id}.jpg",
                    "start": 0,
                    "end": 0.8,
                    "observations": [
                        {"time": t, "confidence": 0.9, "box": box} for t in [0, 0.2, 0.4, 0.6]
                    ],
                }
            )
        index = {
            "duration": 0.8,
            "video_sha256": "test-video-hash",
            "tracks": tracks,
            "samples": [{"time": 0, "thumbnail": "scene-0.jpg"}],
        }
        (directory / "index.json").write_text(json.dumps(index))
        np.save(directory / "embeddings.npy", np.array([[1.0, 0.0, 0.0]]))
        store.update(job_id, status="ready")
        jobs.append(job_id)
    with TestClient(app) as http:
        yield http, store, jobs, encoder


def test_library_search_carries_source_and_respects_scope(workspace):
    http, _, jobs, _ = workspace
    response = http.get("/api/search", params={"q": "red car", "scope": "library"}).json()
    assert response["videos_searched"] == 2
    assert {item["video_id"] for item in response["results"]} == set(jobs)
    response = http.get(
        "/api/search", params={"q": "red car", "scope": "video", "video_id": jobs[0]}
    ).json()
    assert response["videos_searched"] == 1
    assert {item["video_id"] for item in response["results"]} == {jobs[0]}


def test_semantic_query_is_encoded_once_for_the_library(workspace):
    http, _, _, encoder = workspace
    response = http.get("/api/search", params={"q": "a vehicle in a field", "scope": "library"})
    assert response.status_code == 200
    assert encoder.calls == 1


def test_crop_vectors_are_cached_and_reference_is_excluded(workspace):
    http, store, jobs, encoder = workspace
    data = store.load(jobs[0])
    ids, vectors = object_vectors(store.runs / jobs[0], data, encoder)
    calls = encoder.calls
    ids_again, vectors_again = object_vectors(store.runs / jobs[0], data, encoder)
    assert ids_again == ids and np.allclose(vectors, vectors_again)
    assert encoder.calls == calls
    response = http.post(
        "/api/similar", json={"video_id": jobs[0], "track_id": 1, "scope": "library"}
    )
    assert response.status_code == 200
    result = response.json()
    assert result["mode"] == "visual"
    assert result["results"][0]["video_id"] == jobs[1]
    assert result["results"][0]["track_id"] == 1
    assert all((item["video_id"], item["track_id"]) != (jobs[0], 1) for item in result["results"])
    assert "not verified identity" in result["note"]


def test_visual_category_filter_and_gaps():
    tracks = [
        {
            "id": i,
            "class_id": cls,
            "color": "blue",
            "label": "object",
            "observations": [
                {"time": t, "confidence": 0.9, "box": [0, 0, 10, 10]} for t in [0, 0.2, 2, 2.2]
            ],
        }
        for i, cls in [(1, 2), (2, 0)]
    ]
    found = visual_matches(
        {"tracks": tracks, "duration": 3}, [1, 2], np.eye(2), np.array([1.0, 0.0]), 2, True
    )
    assert len(found) == 2
    assert all(item["track_id"] == 1 for item in found)
    assert found[0]["end"] < found[1]["start"]


def test_board_idempotency_notes_persistence_export_and_removal(workspace):
    http, store, jobs, _ = workspace
    body = {
        "video_id": jobs[0],
        "start": 0,
        "end": 0.6,
        "seek": 0.2,
        "track_id": 1,
        "kind": "object",
        "query": "red car",
    }
    first = http.post("/api/board", json=body)
    assert first.status_code == 201
    moment = first.json()
    again = http.post("/api/board", json=body).json()
    assert moment["id"] == again["id"]
    assert len(http.get("/api/board").json()) == 1
    assert len(list((store.runs / jobs[0]).glob("saved-*.jpg"))) == 1
    assert (
        http.patch(
            "/api/board/" + moment["id"],
            json={"note": "<script>bad()</script>", "title": "Reviewed"},
        ).status_code
        == 200
    )
    persisted = Board(store.root / "workspace.sqlite").all()
    assert persisted[0]["title"] == "Reviewed"
    html = http.get("/api/board-export")
    assert "<script>bad()" not in html.text
    assert "&lt;script&gt;" in html.text
    assert "data:image/jpeg;base64," in html.text
    assert "attachment" in html.headers["content-disposition"]
    assert f"http://testserver/?video={jobs[0]}&amp;at=0.200" in html.text
    assert http.delete("/api/board/" + moment["id"]).status_code == 204
    assert http.get("/api/board").json() == []


def test_invalid_moments_and_nonfinite_ranges(workspace):
    http, _, jobs, _ = workspace
    assert (
        http.post(
            "/api/board", json={"video_id": jobs[0], "start": 0, "end": 0.6, "seek": 0.7}
        ).status_code
        == 422
    )
    assert (
        http.post(
            "/api/board",
            json={"video_id": jobs[0], "start": 0, "end": 0.6, "seek": 0.2, "track_id": 999},
        ).status_code
        == 404
    )
    assert (
        http.get("/api/search", params={"q": "car", "scope": "library", "start": "nan"}).status_code
        == 422
    )
    assert http.post("/api/similar", json={"video_id": jobs[0], "track_id": 999}).status_code == 404


def test_report_without_image_is_still_readable(tmp_path):
    html = render_board(
        [
            {
                "video_id": "id",
                "snapshot": "missing.jpg",
                "kind": "manual",
                "start": 0,
                "end": 2,
                "title": "Note",
                "video_name": "Demo",
                "track_id": None,
                "note": "Reviewed locally",
                "query": "",
                "video_sha256": "abc",
            }
        ],
        tmp_path,
    )
    assert "Reviewed locally" in html and "SHA-256: abc" in html
