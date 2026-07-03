# Paiziq ingest service — production container image.
# Build:  docker build -t paiziq-ingest services/ingest
# Run:    docker run -e PAIZIQ_ENV=production -e PAIZIQ_INGEST_DB=/data/ingest.db \
#                    -e PAIZIQ_INGEST_KEYS=<keys> -v paiziq-data:/data -p 8800:8800 paiziq-ingest

FROM python:3.12-slim

WORKDIR /srv/ingest

COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt

COPY app.py config.py storage.py migrations.py ./
COPY migrations/ migrations/

# Non-root runtime user; /data volume for the SQLite database.
RUN useradd --system --create-home paiziq && mkdir -p /data && chown paiziq /data
USER paiziq
VOLUME ["/data"]

EXPOSE 8800
CMD ["python3", "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8800"]
