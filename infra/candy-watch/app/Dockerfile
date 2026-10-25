# Candy watch. Built on the laptop for gmk:  ./jasbros candy build
FROM python:3.14-slim

RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin candy
WORKDIR /app
COPY candy_watch.py ./

USER 10001
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
EXPOSE 8000
CMD ["python", "candy_watch.py"]
