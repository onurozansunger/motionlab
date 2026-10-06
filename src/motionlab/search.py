"""Grounded object filters and explicitly labeled semantic scene retrieval."""

import re
import unicodedata

import numpy as np

from .vision import CLASSES

CLASS_ALIASES = {
    "araba": [2],
    "otomobil": [2],
    "car": [2],
    "arac": [1, 2, 3, 5, 7],
    "vehicle": [1, 2, 3, 5, 7],
    "kamyon": [7],
    "truck": [7],
    "otobus": [5],
    "bus": [5],
    "motor": [3],
    "motosiklet": [3],
    "motorcycle": [3],
    "bisiklet": [1],
    "bicycle": [1],
    "insan": [0],
    "kisi": [0],
    "person": [0],
    "yaya": [0],
    "kopek": [16],
    "dog": [16],
    "kedi": [15],
    "cat": [15],
    "canta": [24, 26, 28],
    "bag": [24, 26, 28],
    "backpack": [24],
    "bavul": [28],
    "suitcase": [28],
    "sandalye": [56],
    "chair": [56],
    "masa": [60],
    "table": [60],
    "sise": [39],
    "bottle": [39],
    "telefon": [67],
    "phone": [67],
    "laptop": [63],
    "kitap": [73],
    "book": [73],
    "kus": [14],
    "bird": [14],
    "tekne": [8],
    "boat": [8],
    "tren": [6],
    "train": [6],
    "ucak": [4],
    "airplane": [4],
    "semsiye": [25],
    "umbrella": [25],
    "bardak": [41],
    "cup": [41],
    "koltuk": [57],
    "couch": [57],
}
COLOR_ALIASES = {
    "kirmizi": "red",
    "red": "red",
    "mavi": "blue",
    "blue": "blue",
    "yesil": "green",
    "green": "green",
    "sari": "yellow",
    "yellow": "yellow",
    "siyah": "black",
    "black": "black",
    "beyaz": "white",
    "white": "white",
    "gri": "gray",
    "gray": "gray",
    "grey": "gray",
    "gumus": "gray",
    "silver": "gray",
    "turuncu": "orange",
    "orange": "orange",
    "mor": "purple",
    "purple": "purple",
}
FILLERS = {"bir", "the", "a", "an", "olan", "goster", "bul", "show", "find", "ve", "and"}

# Preserve aliases such as "bag" and disambiguate color-only words such as "orange".
for _class_id, _name in enumerate(CLASSES):
    if " " not in _name and _name not in COLOR_ALIASES:
        CLASS_ALIASES.setdefault(_name, [_class_id])


def tokens(query: str) -> set[str]:
    text = query.casefold().replace("ı", "i")
    text = "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))
    return set(re.findall(r"[a-z0-9]+", text))


def parse_query(query: str):
    words = set()
    for word in tokens(query):
        if word not in CLASS_ALIASES:
            for suffix in ("lar", "ler", "s"):
                if word.endswith(suffix) and word[: -len(suffix)] in CLASS_ALIASES:
                    word = word[: -len(suffix)]
                    break
        words.add(word)
    classes = set()
    colors = set()
    for word in words:
        classes.update(CLASS_ALIASES.get(word, []))
        if word in COLOR_ALIASES:
            colors.add(COLOR_ALIASES[word])
    simple = bool(classes or colors) and not (
        words - CLASS_ALIASES.keys() - COLOR_ALIASES.keys() - FILLERS
    )
    return classes, colors, simple


def intervals(observations: list[dict], gap: float = 0.8) -> list[list[dict]]:
    groups = []
    for item in sorted(observations, key=lambda observation: observation["time"]):
        if not groups or item["time"] - groups[-1][-1]["time"] > gap:
            groups.append([])
        groups[-1].append(item)
    return groups


def search_index(
    index: dict,
    vectors: np.ndarray,
    query: str,
    encoder,
    start: float = 0,
    end: float | None = None,
    query_vector: np.ndarray | None = None,
) -> dict:
    end = index["duration"] if end is None else min(end, index["duration"])
    classes, colors, simple = parse_query(query)
    if simple:
        matches = []
        for track in index["tracks"]:
            if classes and track["class_id"] not in classes:
                continue
            if colors and track["color"] not in colors:
                continue
            observations = [o for o in track["observations"] if start <= o["time"] <= end]
            for group in intervals(observations):
                if len(group) < 2:
                    continue
                representative = max(group, key=lambda item: item["confidence"])
                matches.append(
                    {
                        "start": group[0]["time"],
                        "end": min(end, group[-1]["time"] + 0.2),
                        "seek": representative["time"],
                        "track_id": track["id"],
                        "label": track["label"],
                        "color": track["color"],
                        "box": representative["box"],
                        "kind": "object",
                        "score": round(float(np.mean([o["confidence"] for o in group])), 4),
                        "thumbnail": track["thumbnail"],
                    }
                )
        matches.sort(key=lambda match: (-match["score"], match["start"]))
        return {
            "mode": "object",
            "results": matches[:30],
            "total": len(matches),
            "note": "Detected object and approximate color match. Track IDs apply only within this video.",
        }

    if query_vector is None:
        query_vector = encoder.encode(text=query)[0]
    scores = vectors @ query_vector
    candidates = [i for i, sample in enumerate(index["samples"]) if start <= sample["time"] <= end]
    candidates.sort(key=lambda i: -float(scores[i]))
    selected = []
    for i in candidates:
        sample = index["samples"][i]
        if any(abs(sample["time"] - match["seek"]) < 2.5 for match in selected):
            continue
        selected.append(
            {
                "start": max(start, sample["time"] - 1),
                "end": min(end, sample["time"] + 1.5),
                "seek": sample["time"],
                "track_id": None,
                "label": "Scene match",
                "color": None,
                "box": None,
                "kind": "semantic",
                "score": round(float(scores[i]), 4),
                "thumbnail": sample["thumbnail"],
            }
        )
        if len(selected) == 8:
            break
    return {
        "mode": "semantic",
        "results": selected,
        "total": len(selected),
        "note": "Ranked by visual similarity; this does not verify an event. Similarity is not a probability.",
    }
