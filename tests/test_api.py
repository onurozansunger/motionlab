import json

import pytest
from fastapi.testclient import TestClient

from motionlab.api import create_app


@pytest.fixture
def client(tmp_path):
    app = create_app(tmp_path)
    with TestClient(app) as client:
        yield client, app.state.store


def test_health_and_missing_video(client):
    http, _ = client
    assert http.get("/health").status_code == 200
    assert http.get("/api/videos/missing").status_code == 404
    assert http.post("/api/demo").status_code == 404


def test_invalid_upload_releases_slot_and_cleans_metadata(client):
    http, store = client
    response = http.post(
        "/api/videos", files={"file": ("invalid.mp4", b"not a video", "video/mp4")}
    )
    assert response.status_code == 422
    assert store.jobs == {}
    assert not list(store.runs.iterdir())
    assert store.busy.acquire(blocking=False)
    store.busy.release()


def test_indexing_queue_is_bounded(client):
    http, store = client
    store.busy.acquire()
    try:
        response = http.post("/api/videos", files={"file": ("file.mp4", b"bytes", "video/mp4")})
        assert response.status_code == 429
    finally:
        store.busy.release()


def test_internal_files_are_not_served(client):
    http, store = client
    job_id = store.create("Example")
    assert http.get(f"/api/videos/{job_id}/assets/job.json").status_code == 404
    assert http.get(f"/api/videos/{job_id}/assets/embeddings.npy").status_code == 404
    assert http.get(f"/api/videos/{job_id}/index").status_code == 409


@pytest.mark.parametrize(
    "body", [{"start": 4, "end": 2}, {"start": -1, "end": 2}, {"start": 0, "end": 12}]
)
def test_clip_ranges_are_validated(client, body):
    http, store = client
    job_id = store.create("Example")
    store.update(job_id, status="ready")
    (store.runs / job_id / "index.json").write_text(json.dumps({"duration": 10}))
    assert http.post(f"/api/videos/{job_id}/clip", json=body).status_code == 422


def test_nonfinite_search_range_rejected(client):
    http, _ = client
    assert (
        http.get("/api/videos/example/search", params={"q": "car", "start": "nan"}).status_code
        == 422
    )
