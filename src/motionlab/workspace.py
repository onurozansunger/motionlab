"""Visual retrieval and a persistent, source-linked review board."""

import base64
import hashlib
import json
import sqlite3
import threading
import uuid
from datetime import UTC, datetime
from html import escape
from pathlib import Path
from typing import Literal

import cv2
import numpy as np
from fastapi import HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict, Field

from .pipeline import thumbnail
from .search import intervals, parse_query, search_index
from .vision import SEMANTIC_MODEL, SEMANTIC_REVISION


class Board:
    def __init__(self, path: Path):
        self.path = path
        with self.connect() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS moments (id TEXT PRIMARY KEY, source_key TEXT UNIQUE, data TEXT NOT NULL)"
            )

    def connect(self):
        return sqlite3.connect(self.path, timeout=10)

    def all(self):
        with self.connect() as db:
            rows = db.execute("SELECT data FROM moments ORDER BY rowid DESC").fetchall()
        return [json.loads(row[0]) for row in rows]

    def save(self, data):
        key = json.dumps(
            [data["video_id"], round(data["start"], 3), round(data["end"], 3), data["track_id"]]
        )
        with self.connect() as db:
            db.execute(
                "INSERT OR IGNORE INTO moments VALUES (?, ?, ?)",
                (data["id"], key, json.dumps(data)),
            )
            row = db.execute("SELECT data FROM moments WHERE source_key = ?", (key,)).fetchone()
        return json.loads(row[0])

    def edit(self, moment_id, values):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT data FROM moments WHERE id = ?", (moment_id,)).fetchone()
            if row is None:
                raise HTTPException(404, "Saved moment not found.")
            data = json.loads(row[0])
            data.update(values)
            db.execute("UPDATE moments SET data = ? WHERE id = ?", (json.dumps(data), moment_id))
        return data

    def remove(self, moment_id):
        with self.connect() as db:
            count = db.execute("DELETE FROM moments WHERE id = ?", (moment_id,)).rowcount
        if not count:
            raise HTTPException(404, "Saved moment not found.")


def object_vectors(directory: Path, index: dict, encoder):
    """Average up to three crop embeddings per track; migrate old indexes on demand."""
    fingerprint = hashlib.sha256(
        json.dumps(
            {
                "video": index["video_sha256"],
                "tracks": index["tracks"],
                "model": SEMANTIC_MODEL,
                "revision": SEMANTIC_REVISION,
                "schema": 1,
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()
    manifest_path = directory / "objects.json"
    vectors_path = directory / "objects.npy"
    if manifest_path.exists() and vectors_path.exists():
        manifest = json.loads(manifest_path.read_text())
        vectors = np.load(vectors_path, allow_pickle=False)
        if manifest["fingerprint"] == fingerprint and len(vectors) == len(manifest["track_ids"]):
            return manifest["track_ids"], vectors
    capture = cv2.VideoCapture(str(directory / "video.mp4"))
    track_ids, vectors = [], []
    try:
        for track in index["tracks"]:
            observations = track["observations"]
            # Confidence-filtered samples spread across the track, not adjacent near-duplicates.
            groups = np.array_split(np.arange(len(observations)), min(3, len(observations)))
            samples = [
                max((observations[int(i)] for i in group), key=lambda o: o["confidence"])
                for group in groups
            ]
            crops = []
            for observation in samples:
                capture.set(cv2.CAP_PROP_POS_MSEC, observation["time"] * 1000)
                ok, frame = capture.read()
                if not ok:
                    continue
                x1, y1, x2, y2 = observation["box"]
                h, w = frame.shape[:2]
                crop = frame[max(0, int(y1)) : min(h, int(y2)), max(0, int(x1)) : min(w, int(x2))]
                if crop.size and min(crop.shape[:2]) >= 8:
                    crops.append(cv2.cvtColor(crop, cv2.COLOR_BGR2RGB))
            if not crops:
                continue
            vector = np.mean(encoder.encode(images=crops), axis=0)
            vector /= max(float(np.linalg.norm(vector)), 1e-9)
            vectors.append(vector)
            track_ids.append(track["id"])
            crop_bgr = cv2.cvtColor(crops[len(crops) // 2], cv2.COLOR_RGB2BGR)
            cv2.imwrite(str(directory / f"crop-{track['id']}.jpg"), crop_bgr)
    finally:
        capture.release()
    matrix = np.asarray(vectors, dtype=np.float32)
    temporary = directory / "objects.tmp.npy"
    np.save(temporary, matrix)
    temporary.replace(vectors_path)
    manifest_path.write_text(json.dumps({"fingerprint": fingerprint, "track_ids": track_ids}))
    return track_ids, matrix


def visual_matches(
    index, ids, vectors, reference_vector, reference_class, same_class, excluded_id=None
):
    matches = []
    if not len(ids):
        return matches
    scores = vectors @ reference_vector
    tracks = {track["id"]: track for track in index["tracks"]}
    for track_id, score in zip(ids, scores, strict=True):
        track = tracks[track_id]
        if track_id == excluded_id or (same_class and track["class_id"] != reference_class):
            continue
        for group in intervals(track["observations"]):
            if len(group) < 2:
                continue
            best = max(group, key=lambda observation: observation["confidence"])
            matches.append(
                {
                    "start": group[0]["time"],
                    "end": min(index["duration"], group[-1]["time"] + 0.2),
                    "seek": best["time"],
                    "track_id": track_id,
                    "label": track["label"],
                    "color": track["color"],
                    "box": best["box"],
                    "thumbnail": f"crop-{track_id}.jpg",
                    "kind": "visual",
                    "score": round(float(score), 4),
                }
            )
    return sorted(matches, key=lambda item: -item["score"])


class VisualRequest(BaseModel):
    video_id: str
    track_id: int = Field(ge=0)
    scope: Literal["video", "library"] = "library"
    same_class: bool = True


class MomentRequest(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)
    video_id: str
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    seek: float = Field(ge=0)
    track_id: int | None = Field(default=None, ge=0)
    kind: Literal["object", "semantic", "visual", "manual"] = "manual"
    query: str = Field(default="", max_length=300)
    title: str = Field(default="", max_length=120)


class MomentEdit(BaseModel):
    title: str | None = Field(default=None, max_length=120)
    note: str | None = Field(default=None, max_length=5000)


def render_board(moments, runs: Path, base_url: str = "http://127.0.0.1:8002/"):
    cards = []
    for moment in moments:
        snapshot = runs / moment["video_id"] / moment["snapshot"]
        image = ""
        if snapshot.exists():
            image = f'<img alt="Saved moment" src="data:image/jpeg;base64,{base64.b64encode(snapshot.read_bytes()).decode()}">'
        cards.append(f"""<article>{image}<section><span class="tag">{escape(moment["kind"].upper())} · {moment["start"]:.2f}–{moment["end"]:.2f}s</span>
<h2>{escape(moment["title"])}</h2><p class="source">{escape(moment["video_name"])} · Track {escape(str(moment["track_id"])) if moment["track_id"] is not None else "—"}</p>
<p class="note">{escape(moment["note"])}</p><p><a href="{escape(base_url, quote=True)}?video={escape(moment["video_id"], quote=True)}&amp;at={moment.get("seek", moment["start"]):.3f}">Open source moment in MotionLab ↗</a></p><p class="meta">Query: {escape(moment["query"]) or "Manual selection"}<br>Video SHA-256: {escape(moment["video_sha256"])}</p></section></article>""")
    return (
        """<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>MotionLab review board</title>
<style>body{font:14px system-ui;background:#f5f5f0;color:#30362e;max-width:950px;margin:40px auto;padding:0 24px}h1{font-weight:550}header p,.source,.meta{color:#78806e}.tag{font-size:11px;letter-spacing:1px;color:#a2643e}article{background:white;border:1px solid #dfe3d7;border-radius:10px;overflow:hidden;margin:24px 0;break-inside:avoid}img{width:100%;max-height:360px;object-fit:contain;background:#1d251f}section{padding:24px}h2{font-size:20px}.note{white-space:pre-wrap;line-height:1.7}.meta{font:10px monospace;overflow-wrap:anywhere}@media print{body{margin:0}article{border-color:#ddd}}</style>
<header><h1>MotionLab / Review board</h1><p>Source-linked moments and reviewer notes. Visual similarity is not identity verification; notes are user annotations.</p></header>"""
        + "".join(cards)
        + "</html>"
    )


def install_workspace(app, store):
    board = Board(store.root / "workspace.sqlite")
    visual_lock = threading.Lock()
    app.state.board = board

    def ready(scope, video_id):
        if scope == "video":
            if not video_id:
                raise HTTPException(422, "Choose a video first.")
            store.load(video_id)
            return [store.get(video_id)]
        with store.lock:
            return [dict(job) for job in store.jobs.values() if job["status"] == "ready"]

    @app.get("/api/search")
    def search(
        q: str = Query(min_length=1, max_length=300),
        scope: Literal["video", "library"] = "video",
        video_id: str | None = None,
        start: float = Query(default=0, ge=0, allow_inf_nan=False),
        end: float | None = Query(default=None, ge=0, allow_inf_nan=False),
    ):
        if not q.strip() or (end is not None and end <= start):
            raise HTTPException(422, "Enter a valid query and time range.")
        jobs = ready(scope, video_id)
        simple = parse_query(q)[2]
        query_vector = None if simple or not jobs else store.models()[1].encode(text=q)[0]
        matches = []
        note = "No indexed videos in this scope."
        total = 0
        for job in jobs:
            data = store.load(job["id"])
            vectors = (
                np.empty((0, 0))
                if simple
                else np.load(store.runs / job["id"] / "embeddings.npy", allow_pickle=False)
            )
            result = search_index(data, vectors, q, None, start, end, query_vector=query_vector)
            note = result["note"]
            total += result["total"]
            matches.extend(
                {**match, "video_id": job["id"], "video_name": job["name"]}
                for match in result["results"]
            )
        matches.sort(key=lambda item: -item["score"])
        return {
            "mode": "object" if simple else "semantic",
            "results": matches[:60],
            "total": total,
            "videos_searched": len(jobs),
            "note": note,
        }

    @app.post("/api/similar")
    def similar(request: VisualRequest):
        source = store.load(request.video_id)
        track = next((track for track in source["tracks"] if track["id"] == request.track_id), None)
        if track is None:
            raise HTTPException(404, "Reference track not found.")
        if not visual_lock.acquire(blocking=False):
            raise HTTPException(429, "Another visual search is running. Try again shortly.")
        try:
            encoder = store.models()[1]
            ids, vectors = object_vectors(store.runs / request.video_id, source, encoder)
            if request.track_id not in ids:
                raise HTTPException(422, "This object is too small or could not be decoded.")
            reference = vectors[ids.index(request.track_id)]
            jobs = ready(request.scope, request.video_id)
            matches = []
            for job in jobs:
                data = store.load(job["id"])
                ids, vectors = object_vectors(store.runs / job["id"], data, encoder)
                found = visual_matches(
                    data,
                    ids,
                    vectors,
                    reference,
                    track["class_id"],
                    request.same_class,
                    request.track_id if job["id"] == request.video_id else None,
                )
                matches.extend(
                    {**item, "video_id": job["id"], "video_name": job["name"]} for item in found
                )
            matches.sort(key=lambda item: -item["score"])
            return {
                "mode": "visual",
                "results": matches[:60],
                "total": len(matches),
                "videos_searched": len(jobs),
                "reference": {
                    "video_id": request.video_id,
                    "track_id": request.track_id,
                    "thumbnail": f"crop-{request.track_id}.jpg",
                },
                "note": "Similar appearance, not verified identity. The reference track is excluded; scores are cosine similarities.",
            }
        finally:
            visual_lock.release()

    @app.get("/api/board")
    def list_board():
        return board.all()

    @app.post("/api/board", status_code=201)
    def save_moment(request: MomentRequest):
        data = store.load(request.video_id)
        if not (request.start <= request.seek < request.end <= data["duration"] + 0.001):
            raise HTTPException(422, "The saved moment must fall inside its video interval.")
        track = None
        if request.track_id is not None:
            track = next((item for item in data["tracks"] if item["id"] == request.track_id), None)
            if track is None:
                raise HTTPException(404, "Track not found.")
        moment_id = uuid.uuid4().hex
        snapshot_name = f"saved-{moment_id}.jpg"
        directory = store.runs / request.video_id
        capture = cv2.VideoCapture(str(directory / "video.mp4"))
        try:
            capture.set(cv2.CAP_PROP_POS_MSEC, request.seek * 1000)
            ok, frame = capture.read()
            if not ok:
                raise HTTPException(422, "Could not decode this moment.")
        finally:
            capture.release()
        box = None
        if track:
            observation = min(
                track["observations"], key=lambda item: abs(item["time"] - request.seek)
            )
            if abs(observation["time"] - request.seek) <= 0.25:
                box = observation["box"]
        thumbnail(frame, directory / snapshot_name, box)
        moment = {
            "id": moment_id,
            **request.model_dump(),
            "video_name": store.get(request.video_id)["name"],
            "title": request.title.strip()
            or (f"{track['color']} {track['label']}" if track else "Scene moment"),
            "note": "",
            "snapshot": snapshot_name,
            "video_sha256": data["video_sha256"],
            "created_at": datetime.now(UTC).isoformat(),
        }
        saved = board.save(moment)
        if saved["id"] != moment_id:
            (directory / snapshot_name).unlink(missing_ok=True)
        return saved

    @app.patch("/api/board/{moment_id}")
    def edit_moment(moment_id: str, request: MomentEdit):
        return board.edit(moment_id, request.model_dump(exclude_none=True))

    @app.delete("/api/board/{moment_id}", status_code=204)
    def remove_moment(moment_id: str):
        board.remove(moment_id)

    @app.get("/api/board-export", response_class=HTMLResponse)
    def export_board(request: Request):
        return HTMLResponse(
            render_board(board.all(), store.runs, str(request.base_url)),
            headers={"Content-Disposition": 'attachment; filename="motionlab-review-board.html"'},
        )
