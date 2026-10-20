FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    ACTIVITY_HEAT_CONFIG=/app/config/sports.toml \
    ACTIVITY_HEAT_DB=/data/activities.db

WORKDIR /app

RUN pip install \
    "datasette>=0.65,<0.66" \
    "datasette-cluster-map>=0.18" \
    "datasette-leaflet-geojson>=0.8" \
    "datasette-vega>=0.6"

COPY pyproject.toml ./
COPY src ./src
RUN pip install ".[timezones]"

COPY config ./config
COPY datasette ./datasette

VOLUME ["/data"]
EXPOSE 8001

CMD ["datasette", "serve", "/data/activities.db", "--create", \
     "--metadata", "/app/datasette/metadata.yml", \
     "--host", "0.0.0.0", "--port", "8001", \
     "--setting", "sql_time_limit_ms", "15000", \
     "--setting", "max_returned_rows", "5000", \
     "--setting", "facet_time_limit_ms", "1000"]
