"""Pinned local inference: YOLOX-S and multilingual SigLIP 2."""

import hashlib
import os
import threading
import urllib.request
from pathlib import Path

os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
os.environ.setdefault("OMP_NUM_THREADS", "2")

import cv2
import numpy as np
import onnxruntime as ort
import supervision as sv

MODEL_URL = "https://github.com/Megvii-BaseDetection/YOLOX/releases/download/0.1.1rc0/yolox_s.onnx"
MODEL_SHA = "c5c2d13e59ae883e6af3b45daea64af4833a4951c92d116ec270d9ddbe998063"
SEMANTIC_MODEL = "google/siglip2-base-patch16-224"
SEMANTIC_REVISION = "75de2d55ec2d0b4efc50b3e9ad70dba96a7b2fa2"
CLASSES = [
    "person",
    "bicycle",
    "car",
    "motorcycle",
    "airplane",
    "bus",
    "train",
    "truck",
    "boat",
    "traffic light",
    "fire hydrant",
    "stop sign",
    "parking meter",
    "bench",
    "bird",
    "cat",
    "dog",
    "horse",
    "sheep",
    "cow",
    "elephant",
    "bear",
    "zebra",
    "giraffe",
    "backpack",
    "umbrella",
    "handbag",
    "tie",
    "suitcase",
    "frisbee",
    "skis",
    "snowboard",
    "sports ball",
    "kite",
    "baseball bat",
    "baseball glove",
    "skateboard",
    "surfboard",
    "tennis racket",
    "bottle",
    "wine glass",
    "cup",
    "fork",
    "knife",
    "spoon",
    "bowl",
    "banana",
    "apple",
    "sandwich",
    "orange",
    "broccoli",
    "carrot",
    "hot dog",
    "pizza",
    "donut",
    "cake",
    "chair",
    "couch",
    "potted plant",
    "bed",
    "dining table",
    "toilet",
    "tv",
    "laptop",
    "mouse",
    "remote",
    "keyboard",
    "cell phone",
    "microwave",
    "oven",
    "toaster",
    "sink",
    "refrigerator",
    "book",
    "clock",
    "vase",
    "scissors",
    "teddy bear",
    "hair drier",
    "toothbrush",
]


def prepare_models(root: Path):
    from huggingface_hub import snapshot_download

    root.mkdir(parents=True, exist_ok=True)
    model = root / "yolox_s.onnx"
    if not model.exists():
        temporary = model.with_suffix(".part")
        urllib.request.urlretrieve(MODEL_URL, temporary)
        if hashlib.sha256(temporary.read_bytes()).hexdigest() != MODEL_SHA:
            temporary.unlink(missing_ok=True)
            raise ValueError("YOLOX model checksum mismatch")
        temporary.replace(model)
    if hashlib.sha256(model.read_bytes()).hexdigest() != MODEL_SHA:
        raise ValueError("YOLOX model checksum mismatch")
    snapshot_download(
        SEMANTIC_MODEL,
        revision=SEMANTIC_REVISION,
        allow_patterns=["*.json", "*.model", "*.safetensors"],
    )


class Detector:
    def __init__(self, model: Path):
        if hashlib.sha256(model.read_bytes()).hexdigest() != MODEL_SHA:
            raise ValueError("Detector integrity check failed; run motionlab setup")
        options = ort.SessionOptions()
        options.intra_op_num_threads = 2
        options.inter_op_num_threads = 1
        self.session = ort.InferenceSession(
            str(model), sess_options=options, providers=["CPUExecutionProvider"]
        )
        self.grids = []
        self.strides = []
        for stride in (8, 16, 32):
            y, x = np.mgrid[: 640 // stride, : 640 // stride]
            self.grids.append(np.stack([x, y], -1).reshape(-1, 2))
            self.strides.append(np.full((x.size, 1), stride))
        self.grid = np.concatenate(self.grids)
        self.stride = np.concatenate(self.strides)

    def detect(self, frame: np.ndarray) -> sv.Detections:
        h, w = frame.shape[:2]
        ratio = min(640 / h, 640 / w)
        padded = np.full((640, 640, 3), 114, dtype=np.uint8)
        resized = cv2.resize(frame, (int(w * ratio), int(h * ratio)))
        padded[: resized.shape[0], : resized.shape[1]] = resized
        tensor = np.ascontiguousarray(padded.transpose(2, 0, 1), dtype=np.float32)[None]
        pred = self.session.run(None, {self.session.get_inputs()[0].name: tensor})[0][0]
        centers = (pred[:, :2] + self.grid) * self.stride
        sizes = np.exp(np.clip(pred[:, 2:4], -20, 20)) * self.stride
        scores = pred[:, 4:5] * pred[:, 5:]
        classes = scores.argmax(axis=1)
        confidence = scores[np.arange(len(scores)), classes]
        valid = confidence >= 0.1
        boxes = np.concatenate([centers - sizes / 2, centers + sizes / 2], 1)[valid] / ratio
        boxes[:, [0, 2]] = np.clip(boxes[:, [0, 2]], 0, w - 1)
        boxes[:, [1, 3]] = np.clip(boxes[:, [1, 3]], 0, h - 1)
        detections = sv.Detections(
            xyxy=boxes.astype(np.float32), confidence=confidence[valid], class_id=classes[valid]
        )
        return detections.with_nms(threshold=0.45, class_agnostic=True)


def dominant_color(frame: np.ndarray, box: list[float]) -> str:
    """Approximate central-crop color; not a segmentation or paint classifier."""
    h, w = frame.shape[:2]
    x1, y1, x2, y2 = box
    dx, dy = (x2 - x1) * 0.15, (y2 - y1) * 0.1
    crop = frame[
        max(0, int(y1 + dy)) : min(h, int(y2 - dy)), max(0, int(x1 + dx)) : min(w, int(x2 - dx))
    ]
    if crop.size == 0:
        return "unknown"
    hsv = cv2.cvtColor(cv2.resize(crop, (32, 32)), cv2.COLOR_BGR2HSV).reshape(-1, 3)
    hue, saturation, value = hsv.T
    if np.mean((saturation < 55) & (value > 165)) > 0.30:
        return "white"
    colored = (saturation > 65) & (value > 45)
    if colored.mean() < 0.22:
        brightness = float(np.median(value))
        return "black" if brightness < 65 else "white" if brightness > 175 else "gray"
    hue = hue[colored]
    bins = {
        "red": (hue < 10) | (hue >= 170),
        "orange": (hue >= 10) & (hue < 23),
        "yellow": (hue >= 23) & (hue < 36),
        "green": (hue >= 36) & (hue < 86),
        "blue": (hue >= 86) & (hue < 130),
        "purple": (hue >= 130) & (hue < 170),
    }
    return max(bins, key=lambda name: int(bins[name].sum()))


class SemanticEncoder:
    def __init__(self):
        import torch
        from transformers import AutoModel, AutoProcessor

        torch.set_num_threads(2)
        self.torch = torch
        self.lock = threading.Lock()
        self.processor = AutoProcessor.from_pretrained(
            SEMANTIC_MODEL, revision=SEMANTIC_REVISION, local_files_only=True, use_fast=False
        )
        self.model = AutoModel.from_pretrained(
            SEMANTIC_MODEL, revision=SEMANTIC_REVISION, local_files_only=True
        ).eval()

    def encode(self, *, images=None, text=None) -> np.ndarray:
        with self.lock, self.torch.inference_mode():
            if images is not None:
                inputs = self.processor(images=images, return_tensors="pt")
                output = self.model.get_image_features(**inputs)
            else:
                inputs = self.processor(
                    text=[text],
                    padding="max_length",
                    truncation=True,
                    max_length=64,
                    return_tensors="pt",
                )
                output = self.model.get_text_features(**inputs)
            if hasattr(output, "pooler_output"):
                output = output.pooler_output
            vectors = output.float().cpu().numpy()
            return vectors / np.maximum(np.linalg.norm(vectors, axis=1, keepdims=True), 1e-9)
