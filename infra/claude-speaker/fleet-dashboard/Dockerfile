# The speaker fleet dashboard. Built on the laptop for gmk:
#     ./jasbros dashboard build
FROM python:3.14-slim

RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin fleet
WORKDIR /app
COPY fleet.py page.html ./

USER 10001
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
EXPOSE 8000
CMD ["python", "fleet.py"]
