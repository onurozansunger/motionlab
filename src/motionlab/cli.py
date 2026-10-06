import argparse
import os
import subprocess
import urllib.request
from pathlib import Path

import imageio_ffmpeg

from .vision import prepare_models


def main():
    parser = argparse.ArgumentParser(description="MotionLab video memory")
    parser.add_argument("command", choices=["setup", "demo-video", "serve"])
    parser.add_argument("--port", type=int, default=8002)
    args = parser.parse_args()
    root = Path(os.getenv("MOTIONLAB_HOME", ".")).resolve()
    if args.command == "setup":
        prepare_models(root / "models")
        print("Models ready.")
    elif args.command == "demo-video":
        data = root / "data"
        data.mkdir(exist_ok=True)
        source = data / "vehicles-source.mp4"
        if not source.exists():
            urllib.request.urlretrieve(
                "https://media.roboflow.com/supervision/video-examples/vehicles.mp4", source
            )
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
                "12",
                "-vf",
                "scale=1280:-2,fps=25",
                "-an",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-movflags",
                "+faststart",
                str(data / "demo.mp4"),
            ],
            check=True,
            timeout=120,
        )
        print("Demo ready. Source: Roboflow Supervision example video; not an evaluation set.")
    else:
        import uvicorn

        uvicorn.run("motionlab.api:app", host="127.0.0.1", port=args.port)
