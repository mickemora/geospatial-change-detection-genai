# Change detector task for ECS Fargate (linux/amd64).
# rasterio wheels bundle GDAL, but GDAL still links against the system libexpat,
# which the slim base image no longer ships.
FROM public.ecr.aws/docker/library/python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends libexpat1 ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY detector ./detector

# Fail the build (not the Fargate task) if any import is broken.
RUN python -c "import detector.main"

RUN useradd --create-home --uid 10001 detector
USER detector

ENTRYPOINT ["python", "-m", "detector.main"]
