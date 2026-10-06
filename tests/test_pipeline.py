import json

import cv2
import numpy as np
import pytest
import supervision as sv

from motionlab.pipeline import analyze, inspect_video


def test_video_pipeline_persists_seekable_index_and_exports(tmp_path):
    source = tmp_path / "input.mp4"
    writer = cv2.VideoWriter(str(source), cv2.VideoWriter_fourcc(*"mp4v"), 25, (160, 96))
    for i in range(50):
        image = np.zeros((96, 160, 3), dtype=np.uint8)
        cv2.rectangle(image, (20 + i // 5, 20), (70 + i // 5, 65), (255, 0, 0), -1)
        writer.write(image)
    writer.release()
    assert inspect_video(source)["duration"] == 2
    source.rename(tmp_path / "source")

    class Detector:
        frame = 0

        def detect(self, image):
            x = self.frame
            self.frame += 1
            return sv.Detections(
                xyxy=np.array([[20 + x, 20, 70 + x, 65]], dtype=np.float32),
                confidence=np.array([0.95]),
                class_id=np.array([2]),
            )

    class Encoder:
        def encode(self, *, images):
            return np.tile([1.0, 0.0], (len(images), 1))

    progress = []
    index = analyze(tmp_path, Detector(), Encoder(), lambda **values: progress.append(values))
    assert len(index["tracks"]) == 1
    assert len(index["samples"]) == 2
    assert index["tracks"][0]["color"] == "blue"
    assert (tmp_path / "tracks.csv").read_text().startswith("track_id,class,")
    assert json.loads((tmp_path / "index.json").read_text())["schema_version"] == 1
    assert np.load(tmp_path / "embeddings.npy").shape == (2, 2)
    assert inspect_video(tmp_path / "video.mp4")["duration"] == pytest.approx(2, abs=1 / 25 + 1e-6)
    assert not (tmp_path / "source").exists()
