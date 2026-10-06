import json
import logging
import math
import os
import re
import shutil
import subprocess
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path

import imageio_ffmpeg
import numpy as np
from fastapi import FastAPI, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from .pipeline import analyze, inspect_video
from .search import parse_query, search_index
from .vision import Detector, SemanticEncoder
from .workspace import install_workspace

ROOT = Path(os.getenv("MOTIONLAB_HOME", ".")).resolve()
STATIC = Path(__file__).parent / "static"
MAX_UPLOAD = 200 * 1024 * 1024
logger = logging.getLogger("motionlab")


class Store:
    def __init__(self, root: Path):
        self.root = root
        self.runs = root / "runs"
        self.runs.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.busy = threading.BoundedSemaphore(1)
        self.exports = threading.BoundedSemaphore(2)
        self.pool = ThreadPoolExecutor(max_workers=1)
        self.jobs = {}
        self.detector = None
        self.encoder = None
        for path in self.runs.glob("*/job.json"):
            try:
                job = json.loads(path.read_text())
                if job["status"] not in {"ready", "error"}:
                    job.update(
                        status="error", stage="Processing was interrupted by a server restart."
                    )
                self.jobs[job["id"]] = job
            except (ValueError, KeyError):
                logger.warning("Invalid job metadata: %s", path.name)

    def update(self, job_id, **values):
        with self.lock:
            self.jobs[job_id].update(values)
            path = self.runs / job_id / "job.json"
            temporary = path.with_suffix(".tmp")
            temporary.write_text(json.dumps(self.jobs[job_id], ensure_ascii=False))
            temporary.replace(path)

    def create(self, name):
        job_id = uuid.uuid4().hex
        (self.runs / job_id).mkdir()
        with self.lock:
            self.jobs[job_id] = {
                "id": job_id,
                "name": name[:100],
                "status": "processing",
                "progress": 0,
                "stage": "Receiving video",
                "track_count": 0,
            }
        self.update(job_id)
        return job_id

    def get(self, job_id):
        with self.lock:
            if job_id not in self.jobs:
                raise HTTPException(404, "Video not found.")
            return dict(self.jobs[job_id])

    def load(self, job_id):
        if self.get(job_id)["status"] != "ready":
            raise HTTPException(409, "The video index is not ready yet.")
        return json.loads((self.runs / job_id / "index.json").read_text())

    def models(self):
        with self.lock:
            if self.detector is None:
                self.detector = Detector(self.root / "models/yolox_s.onnx")
            if self.encoder is None:
                self.encoder = SemanticEncoder()
        return self.detector, self.encoder

    def process(self, job_id):
        try:
            self.update(job_id, stage="Loading models", progress=0.01)
            detector, encoder = self.models()
            index = analyze(
                self.runs / job_id,
                detector,
                encoder,
                lambda **values: self.update(job_id, **values),
            )
            self.update(
                job_id,
                status="ready",
                stage="Ready",
                progress=1,
                duration=index["duration"],
                width=index["width"],
                height=index["height"],
                track_count=len(index["tracks"]),
                sample_count=len(index["samples"]),
                processing_seconds=index["processing_seconds"],
            )
        except Exception:
            logger.exception("Video processing failed: %s", job_id)
            self.update(
                job_id,
                status="error",
                stage="Processing failed. Check the model setup and video format.",
            )
        finally:
            self.busy.release()


class ClipRequest(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)
    start: float = Field(ge=0)
    end: float = Field(gt=0)


def create_app(root: Path = ROOT):
    store = Store(root)

    @asynccontextmanager
    async def lifespan(app):
        yield
        store.pool.shutdown(wait=True)

    app = FastAPI(title="MotionLab", version="0.2.0", lifespan=lifespan)
    app.state.store = store

    @app.middleware("http")
    async def headers(request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @app.get("/")
    def home():
        return FileResponse(STATIC / "index.html")

    @app.get("/health")
    def health():
        return {
            "status": "ok",
            "version": "0.2.0",
            "detector_available": (root / "models/yolox_s.onnx").exists(),
            "demo_available": (root / "data/demo.mp4").exists(),
        }

    @app.get("/api/videos")
    def videos():
        with store.lock:
            return list(reversed([dict(job) for job in store.jobs.values()]))

    @app.post("/api/videos", status_code=202)
    async def upload(file: UploadFile):
        if not store.busy.acquire(blocking=False):
            raise HTTPException(429, "A video is being processed. Wait for it to finish.")
        job_id = None
        submitted = False
        try:
            job_id = store.create(Path(file.filename or "Video").name)
            path = store.runs / job_id / "source"
            total = 0
            with path.open("wb") as handle:
                while chunk := await file.read(1024 * 1024):
                    total += len(chunk)
                    if total > MAX_UPLOAD:
                        raise HTTPException(413, "Videos must be 200 MB or smaller.")
                    handle.write(chunk)
            try:
                inspect_video(path)
            except ValueError as error:
                raise HTTPException(422, str(error)) from error
            store.pool.submit(store.process, job_id)
            submitted = True
            return store.get(job_id)
        finally:
            await file.close()
            if not submitted:
                store.busy.release()
                if job_id:
                    with store.lock:
                        store.jobs.pop(job_id, None)
                    shutil.rmtree(store.runs / job_id)

    @app.post("/api/demo", status_code=202)
    def demo():
        source = root / "data/demo.mp4"
        if not source.exists():
            raise HTTPException(404, "Sample video not installed. Run motionlab demo-video.")
        if not store.busy.acquire(blocking=False):
            raise HTTPException(429, "A video is being processed. Wait for it to finish.")
        try:
            job_id = store.create("Highway · sample video")
            shutil.copyfile(source, store.runs / job_id / "source")
            store.pool.submit(store.process, job_id)
            return store.get(job_id)
        except Exception:
            store.busy.release()
            raise

    @app.get("/api/videos/{job_id}")
    def video(job_id: str):
        return store.get(job_id)

    @app.get("/api/videos/{job_id}/index")
    def index(job_id: str):
        data = store.load(job_id)
        return {key: data[key] for key in ("tracks", "duration", "width", "height", "analysis_fps")}

    @app.get("/api/videos/{job_id}/search")
    def search(
        job_id: str,
        q: str = Query(min_length=1, max_length=300),
        start: float = Query(default=0, ge=0),
        end: float | None = Query(default=None, ge=0),
    ):
        if (
            not q.strip()
            or not math.isfinite(start)
            or (end is not None and not math.isfinite(end))
        ):
            raise HTTPException(422, "Enter a valid query and time range.")
        data = store.load(job_id)
        if end is not None and end <= start:
            raise HTTPException(422, "End time must be after start time.")
        encoder = None if parse_query(q)[2] else store.models()[1]
        vectors = np.load(store.runs / job_id / "embeddings.npy", allow_pickle=False)
        return search_index(data, vectors, q, encoder, start, end)

    @app.get("/api/videos/{job_id}/assets/{name}")
    def asset(job_id: str, name: str):
        store.get(job_id)
        if not re.fullmatch(
            r"(?:video\.mp4|(?:track|crop)-\d+\.jpg|scene-\d+\.jpg|saved-[a-f0-9]{32}\.jpg|clip-\d+-\d+\.mp4)",
            name,
        ):
            raise HTTPException(404, "File not found.")
        path = store.runs / job_id / name
        if not path.is_file():
            raise HTTPException(404, "File is not ready yet.")
        return FileResponse(
            path, filename=f"motionlab-{name}" if name.startswith("clip-") else None
        )

    @app.get("/api/videos/{job_id}/export/{kind}")
    def export(job_id: str, kind: str):
        store.load(job_id)
        if kind not in {"json", "csv"}:
            raise HTTPException(404, "Export not found.")
        return FileResponse(
            store.runs / job_id / ("index.json" if kind == "json" else "tracks.csv"),
            filename=f"motionlab-{job_id[:8]}.{kind}",
        )

    @app.post("/api/videos/{job_id}/clip")
    def clip(job_id: str, request: ClipRequest):
        data = store.load(job_id)
        if request.end <= request.start or request.end > data["duration"] + 0.01:
            raise HTTPException(422, "The clip range must be within the video duration.")
        if not store.exports.acquire(blocking=False):
            raise HTTPException(429, "Other clips are being prepared. Try again shortly.")
        name = f"clip-{round(request.start * 1000)}-{round(request.end * 1000)}.mp4"
        directory = store.runs / job_id
        target = directory / name
        temporary = directory / f"{uuid.uuid4().hex}.mp4"
        try:
            if not target.exists():
                subprocess.run(
                    [
                        imageio_ffmpeg.get_ffmpeg_exe(),
                        "-hide_banner",
                        "-loglevel",
                        "error",
                        "-y",
                        "-ss",
                        str(request.start),
                        "-i",
                        str(directory / "video.mp4"),
                        "-t",
                        str(request.end - request.start),
                        "-an",
                        "-c:v",
                        "libx264",
                        "-threads",
                        "2",
                        "-preset",
                        "fast",
                        "-pix_fmt",
                        "yuv420p",
                        "-movflags",
                        "+faststart",
                        str(temporary),
                    ],
                    check=True,
                    capture_output=True,
                    timeout=90,
                )
                temporary.replace(target)
            return {"url": f"/api/videos/{job_id}/assets/{name}", "filename": f"motionlab-{name}"}
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
            raise HTTPException(500, "Could not export the clip.") from error
        finally:
            temporary.unlink(missing_ok=True)
            store.exports.release()

    install_workspace(app, store)
    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app


app = create_app()
