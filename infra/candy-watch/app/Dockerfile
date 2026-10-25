# Candy watch. Built on the laptop for gmk:  ./jasbros candy build
FROM python:3.14-slim AS base

RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin candy
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# YOLOX-nano (Apache-2.0, Megvii), pinned by checksum: downloaded here, never
# committed. https://github.com/Megvii-BaseDetection/YOLOX
ADD --checksum=sha256:c789161ed43c8269fcd4e67c67eeeb4e80c622da2eb296a20bc6007bd18a0b7d \
    https://github.com/Megvii-BaseDetection/YOLOX/releases/download/0.1.1rc0/yolox_nano.onnx \
    /app/yolox_nano.onnx
RUN chmod 644 /app/yolox_nano.onnx

COPY candy_watch.py vision.py watch.py stream.py ./

# The tests, on the image's own Python and packages. ./jasbros candy build
# builds this stage first; a failure stops the build before anything is pushed.
FROM base AS test
COPY tests/ ./tests/
RUN python -m unittest discover -s tests

FROM base
USER 10001
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
EXPOSE 8000
CMD ["python", "candy_watch.py"]
