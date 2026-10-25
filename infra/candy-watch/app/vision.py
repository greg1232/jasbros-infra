"""Seeing people, on gmk's CPU: the gate in front of Claude.

YOLOX-nano (Apache-2.0, Megvii; 0.9M parameters, ~1 GFLOP) on ONNX Runtime
with one thread. It answers one question cheaply — who is in the frame, and
where — so that Claude is only asked the expensive ones (what costume? how
many candies?) at the moments that matter. See docs/candy-watch.md.

The pre- and post-processing follow YOLOX's own ONNX demo
(demo/ONNXRuntime/onnx_inference.py and yolox/utils/demo_utils.py):
letterbox to 416×416 padded with 114, BGR, raw 0–255; decode boxes on the
stride-8/16/32 grids; score = objectness × class; non-max suppression.

    python vision.py photo.jpg ...          # people, and how long it took
"""

import io
import os
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image

MODEL = Path(os.environ.get("VISION_MODEL", "/app/yolox_nano.onnx"))
SIZE = 416
STRIDES = (8, 16, 32)
PERSON = 0              # COCO class 0


class Detector:
    def __init__(self, model: Path = MODEL, threads: int = 1):
        import onnxruntime as ort

        options = ort.SessionOptions()
        # One thread, as asked: the pod is held to one CPU and this keeps
        # ONNX Runtime from starting a pool that fights itself over it.
        options.intra_op_num_threads = threads
        options.inter_op_num_threads = 1
        options.log_severity_level = 3
        self.session = ort.InferenceSession(str(model), options,
                                            providers=["CPUExecutionProvider"])
        self.input = self.session.get_inputs()[0].name
        self.grids, self.expanded = _grids()
        self.last_ms = 0.0

    def people(self, picture, threshold: float = 0.45) -> list[dict]:
        """Everyone in `picture` (JPEG bytes, a path, or a PIL image), as
        {"box": [x1, y1, x2, y2] in its own pixels, "score": 0–1}."""
        image = _open(picture)
        tensor, ratio = _letterbox(image)
        started = time.perf_counter()
        raw = self.session.run(None, {self.input: tensor})[0][0]
        self.last_ms = (time.perf_counter() - started) * 1000
        return _people(raw, ratio, self.grids, self.expanded, threshold)


def _open(picture) -> Image.Image:
    if isinstance(picture, Image.Image):
        return picture.convert("RGB")
    if isinstance(picture, (bytes, bytearray)):
        return Image.open(io.BytesIO(picture)).convert("RGB")
    return Image.open(picture).convert("RGB")


def _letterbox(image: Image.Image) -> tuple[np.ndarray, float]:
    ratio = min(SIZE / image.height, SIZE / image.width)
    resized = image.resize((int(image.width * ratio), int(image.height * ratio)),
                           Image.BILINEAR)
    canvas = np.full((SIZE, SIZE, 3), 114, dtype=np.uint8)
    canvas[:resized.height, :resized.width] = np.asarray(resized)[:, :, ::-1]
    return canvas.transpose(2, 0, 1)[None].astype(np.float32), ratio


def _grids() -> tuple[np.ndarray, np.ndarray]:
    grids, strides = [], []
    for stride in STRIDES:
        side = SIZE // stride
        xs, ys = np.meshgrid(np.arange(side), np.arange(side))
        grids.append(np.stack((xs, ys), 2).reshape(-1, 2))
        strides.append(np.full((side * side, 1), stride))
    return (np.concatenate(grids).astype(np.float32),
            np.concatenate(strides).astype(np.float32))


def _people(raw: np.ndarray, ratio: float, grids: np.ndarray,
            strides: np.ndarray, threshold: float) -> list[dict]:
    xy = (raw[:, :2] + grids) * strides
    wh = np.exp(raw[:, 2:4]) * strides
    scores = raw[:, 4] * raw[:, 5 + PERSON]
    keep = scores >= threshold
    if not keep.any():
        return []
    xy, wh, scores = xy[keep], wh[keep], scores[keep]
    boxes = np.concatenate((xy - wh / 2, xy + wh / 2), 1) / ratio
    out = []
    for i in _nms(boxes, scores, 0.45):
        out.append({"box": [round(float(v), 1) for v in boxes[i]],
                    "score": round(float(scores[i]), 3)})
    return out


def _nms(boxes: np.ndarray, scores: np.ndarray, overlap: float) -> list[int]:
    x1, y1, x2, y2 = boxes.T
    areas = (x2 - x1) * (y2 - y1)
    order = scores.argsort()[::-1]
    keep = []
    while order.size:
        i = order[0]
        keep.append(int(i))
        xx1 = np.maximum(x1[i], x1[order[1:]])
        yy1 = np.maximum(y1[i], y1[order[1:]])
        xx2 = np.minimum(x2[i], x2[order[1:]])
        yy2 = np.minimum(y2[i], y2[order[1:]])
        inter = np.maximum(0, xx2 - xx1) * np.maximum(0, yy2 - yy1)
        iou = inter / (areas[i] + areas[order[1:]] - inter)
        order = order[1:][iou <= overlap]
    return keep


if __name__ == "__main__":
    detector = Detector(Path(os.environ.get("VISION_MODEL", MODEL)))
    for path in sys.argv[1:]:
        found = detector.people(path)
        print(f"{path}: {len(found)} people in {detector.last_ms:.0f} ms "
              f"{[p['score'] for p in found]}")
