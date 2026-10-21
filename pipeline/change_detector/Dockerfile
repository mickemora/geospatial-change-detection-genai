# Change detector task for ECS Fargate (linux/amd64).
# rasterio wheels bundle GDAL, so no system GDAL install is needed.
FROM public.ecr.aws/docker/library/python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY detector ./detector

RUN useradd --create-home --uid 10001 detector
USER detector

ENTRYPOINT ["python", "-m", "detector.main"]
