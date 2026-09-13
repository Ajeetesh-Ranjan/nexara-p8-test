# NEXARA production image — one image, six services, selected by command.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    NEXARA_DATA=/data

WORKDIR /app

# curl is required by the compose healthchecks
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY services/ /app/services/
COPY research-fabric/ /app/research-fabric/
COPY scripts/ /app/scripts/

# Run as an unprivileged user; /data is chowned by the entrypoint if needed.
RUN useradd --create-home --uid 10001 nexara \
 && mkdir -p /data /backups \
 && chown -R nexara:nexara /app /data /backups
USER nexara

EXPOSE 8080 8081 8082 8083 8084 8085 8086

CMD ["python", "-m", "services.brain_runtime.main"]
