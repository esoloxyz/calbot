FROM ubuntu:24.04@sha256:4fbb8e6a8395de5a7550b33509421a2bafbc0aab6c06ba2cef9ebffbc7092d90 AS builder

ENV DEBIAN_FRONTEND=noninteractive
ENV PIP_DISABLE_PIP_VERSION_CHECK=1
ENV PIP_NO_INPUT=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    python3 \
    python3-pip \
    python3-venv \
    && rm -rf /var/lib/apt/lists/*

RUN python3 -m venv /opt/venv
WORKDIR /build
COPY requirements.lock .
RUN /opt/venv/bin/python -m pip install \
    --no-cache-dir \
    --only-binary=:all: \
    --require-hashes \
    -r requirements.lock

FROM ubuntu:24.04@sha256:4fbb8e6a8395de5a7550b33509421a2bafbc0aab6c06ba2cef9ebffbc7092d90

ENV DEBIAN_FRONTEND=noninteractive
ENV HOME=/home/calbot
ENV PATH=/opt/venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    python3 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 calbot \
    && useradd --uid 10001 --gid 10001 --create-home \
        --home-dir /home/calbot --shell /usr/sbin/nologin calbot \
    && install -d -o root -g root -m 0755 /app

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
COPY --chmod=0444 bot.py ./
COPY --chmod=0444 PERSONALITY.md ./
COPY --chown=root:root calbot/ ./calbot/
COPY --chmod=0555 start.sh ./

RUN chmod -R a-w /app/calbot \
    && python3 -m compileall -q -f /app/calbot

USER calbot:calbot

CMD ["bash", "/app/start.sh"]
