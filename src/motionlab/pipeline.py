import csv
import hashlib
import json
import subprocess
import time
from collections import Counter
from pathlib import Path

import cv2
import imageio_ffmpeg
import numpy as np
from trackers import ByteTrackTracker

from .vision import CLASSES, MODEL_SHA, SEMANTIC_MODEL, SEMANTIC_REVISION, dominant_color

MAX_SECONDS = 90
ANALYSIS_FPS = 5


def inspect_video(path: Path) -> dict:
    capture = cv2.VideoCapture(str(path))
    try:
        fps = capture.get(cv2.CAP_PROP_FPS)
        frames = capture.get(cv2.CAP_PROP_FRAME_COUNT)
        width = capture.get(cv2.CAP_PROP_FRAME_WIDTH)
        height = capture.get(cv2.CAP_PROP_FRAME_HEIGHT)
        if not capture.isOpened() or not all(np.isfinite([fps, frames, width, height])):
            raise ValueError("Could not read the video. Use MP4 or MOV.")
        if fps <= 0 or frames <= 0 or width < 16 or height < 16:
            raise ValueError("Invalid video metadata.")
        if frames / fps > MAX_SECONDS + 0.1:
            raise ValueError(f"This version supports videos up to {MAX_SECONDS} seconds.")
        if width * height > 3840 * 2160 or fps > 120:
            raise ValueError("Videos up to 4K and 120 FPS are supported.")
        ok, _ = capture.read()
        if not ok:
            raise ValueError("Could not decode the first video frame.")
        return {"duration": frames / fps, "fps": fps, "width": int(width), "height": int(height)}
    finally:
        capture.release()


def normalize_video(source: Path, target: Path):
    subprocess.run(
        [
            imageio_ffmpeg.get_ffmpeg_exe(),
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(source),
            "-t",
            str(MAX_SECONDS),
            "-map",
            "0:v:0",
            "-an",
            "-vf",
            "setpts=PTS-STARTPTS,scale=w='min(1280,iw)':h=-2,fps=25:start_time=0:eof_action=pass",
            "-c:v",
            "libx264",
            "-threads",
            "2",
            "-preset",
            "fast",
            "-crf",
            "22",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(target),
        ],
        check=True,
        capture_output=True,
        timeout=180,
    )


def thumbnail(frame: np.ndarray, target: Path, box=None):
    image = frame.copy()
    if box is not None:
        x1, y1, x2, y2 = map(int, box)
        cv2.rectangle(image, (x1, y1), (x2, y2), (105, 235, 165), 3)
    h, w = image.shape[:2]
    image = cv2.resize(image, (480, max(1, round(h * 480 / w))))
    cv2.imwrite(str(target), image, [cv2.IMWRITE_JPEG_QUALITY, 85])


def analyze(directory: Path, detector, encoder, update):
    started = time.perf_counter()
    source = directory / "source"
    original = inspect_video(source)
    update(stage="Preparing video", progress=0.02)
    normalize_video(source, directory / "video.mp4")
    source.unlink()
    video = directory / "video.mp4"
    info = inspect_video(video)
    capture = cv2.VideoCapture(str(video))
    tracker = ByteTrackTracker(
        frame_rate=ANALYSIS_FPS,
        track_activation_threshold=0.35,
        high_conf_det_threshold=0.25,
        minimum_consecutive_frames=2,
        lost_track_buffer=30,
    )
    tracks = {}
    samples, embeddings, pending = [], [], []
    seen_frames = 0
    processed = 0
    frame_step = round(info["fps"] / ANALYSIS_FPS)
    try:
        while seen_frames < int(MAX_SECONDS * info["fps"]):
            ok, frame = capture.read()
            if not ok:
                break
            frame_number = seen_frames
            seen_frames += 1
            if frame_number % frame_step:
                continue
            timestamp = round(frame_number / info["fps"], 3)
            detections = detector.detect(frame)
            detections = tracker.update(detections, timestamp=timestamp)
            processed += 1
            for box, confidence, class_id, track_id in zip(
                detections.xyxy,
                detections.confidence,
                detections.class_id,
                detections.tracker_id,
                strict=True,
            ):
                if int(track_id) < 0:
                    continue
                track_id = int(track_id)
                confidence = float(confidence)
                coordinates = [round(float(value), 1) for value in box]
                color = dominant_color(frame, coordinates)
                record = tracks.setdefault(
                    track_id,
                    {
                        "id": track_id,
                        "observations": [],
                        "classes": [],
                        "colors": [],
                        "best_confidence": 0,
                        "thumbnail": f"track-{track_id}.jpg",
                    },
                )
                record["classes"].append(int(class_id))
                record["colors"].append(color)
                record["observations"].append(
                    {
                        "time": timestamp,
                        "box": coordinates,
                        "confidence": round(confidence, 4),
                    }
                )
                if confidence > record["best_confidence"]:
                    record["best_confidence"] = confidence
                    thumbnail(frame, directory / record["thumbnail"], coordinates)
            if frame_number % round(info["fps"]) == 0:
                name = f"scene-{len(samples):04d}.jpg"
                thumbnail(frame, directory / name)
                samples.append({"time": timestamp, "thumbnail": name})
                pending.append(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
                if len(pending) == 4:
                    embeddings.extend(encoder.encode(images=pending))
                    pending.clear()
            update(
                stage="Indexing objects and scenes",
                progress=min(0.94, 0.07 + 0.87 * timestamp / info["duration"]),
                indexed_seconds=timestamp,
                track_count=len(tracks),
            )
    finally:
        capture.release()
    if pending:
        embeddings.extend(encoder.encode(images=pending))
    if not samples:
        raise ValueError("No video frames could be indexed.")
    finalized = []
    for record in tracks.values():
        if len(record["observations"]) < 3:
            continue
        record["class_id"] = Counter(record.pop("classes")).most_common(1)[0][0]
        record["color"] = Counter(record.pop("colors")).most_common(1)[0][0]
        record["label"] = CLASSES[record["class_id"]]
        record.pop("best_confidence")
        record["start"] = record["observations"][0]["time"]
        record["end"] = min(info["duration"], record["observations"][-1]["time"] + 0.2)
        finalized.append(record)
    index = {
        **info,
        "tracks": finalized,
        "samples": samples,
        "original": original,
        "analysis_fps": ANALYSIS_FPS,
        "semantic_fps": 1,
        "schema_version": 1,
        "detector": {"name": "YOLOX-S", "sha256": MODEL_SHA},
        "semantic": {"name": SEMANTIC_MODEL, "revision": SEMANTIC_REVISION},
        "video_sha256": hashlib.sha256(video.read_bytes()).hexdigest(),
        "processing_seconds": round(time.perf_counter() - started, 2),
        "processed_frames": processed,
    }
    (directory / "index.json").write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")
    np.save(directory / "embeddings.npy", np.asarray(embeddings, dtype=np.float32))
    with (directory / "tracks.csv").open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "track_id",
                "class",
                "approximate_color",
                "time_s",
                "x1",
                "y1",
                "x2",
                "y2",
                "detector_confidence",
            ]
        )
        for track in finalized:
            for observation in track["observations"]:
                writer.writerow(
                    [
                        track["id"],
                        track["label"],
                        track["color"],
                        observation["time"],
                        *observation["box"],
                        observation["confidence"],
                    ]
                )
    return index
