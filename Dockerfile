# syntax=docker/dockerfile:1
FROM golang:1.26.10-bookworm@sha256:632dff7351799a5a9954a48dc06670ade72ee44e8c351cdc0e5a57af2fea21af AS engine
ENV GOMAXPROCS=2 CGO_ENABLED=0
ADD --checksum=sha256:68afd7f68c84bd978966feae2116339aa7bf454b39c573910c462e87c3774d5a https://codeload.github.com/rclone/rclone/tar.gz/refs/tags/v1.75.2 /tmp/rclone.tar.gz
WORKDIR /src
RUN tar -xzf /tmp/rclone.tar.gz --strip-components=1 -C /src
COPY burst-engine/burst-engine.patch /tmp/burst.patch
RUN git apply /tmp/burst.patch && go test -p 1 ./vfs/vfscache/downloaders ./vfs/vfscommon && go build -p 1 -trimpath -ldflags '-s -w -X github.com/rclone/rclone/fs.Version=v1.75.2-burst1' -o /rclone-burst1 ./cmd/plexburst

FROM python:3.12-slim-bookworm@sha256:6a0e06f5ba0b373d051aabd3fffb9b873f27f9a2a3a14bcbe37dee6f0e3bbc70
RUN apt-get update && apt-get install -y --no-install-recommends fuse3 ca-certificates && rm -rf /var/lib/apt/lists/* && mkdir -p /media /state /export
COPY --from=engine /rclone-burst1 /usr/local/bin/rclone-burst1
WORKDIR /app
COPY cache_service.py dashboard_server.py dashboard.html /app/
COPY docker/entrypoint.py docker/healthcheck.py /app/
COPY LICENSE NOTICE.md burst-engine/rclone-COPYING /usr/share/doc/plex-ssd-burst-cache/
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
EXPOSE 18779
HEALTHCHECK --interval=5s --timeout=3s --start-period=30s --retries=3 CMD ["python", "/app/healthcheck.py"]
STOPSIGNAL SIGTERM
ENTRYPOINT ["python", "/app/entrypoint.py"]
