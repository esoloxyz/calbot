FROM ubuntu:24.04@sha256:4fbb8e6a8395de5a7550b33509421a2bafbc0aab6c06ba2cef9ebffbc7092d90

ARG TEMPOUP_COMMIT=96cec1ee6735834d1674f282ef317b708ec6de53
ARG TEMPOUP_SHA256=5a6e26630f804f226264f5da4553c3eb3cb7e15ec387c3392d7f6749422042d9
ARG TEMPO_VERSION=v1.4.3

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python3-pip \
    python3-venv \
    curl \
    ca-certificates \
    sqlite3 \
    gnupg \
    && rm -rf /var/lib/apt/lists/*

RUN sqlite3 --version \
    && python3 -c "import sqlite3; print(sqlite3.sqlite_version)"

WORKDIR /app

RUN python3 -m venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"

COPY requirements.lock .
RUN pip install --no-cache-dir --require-hashes -r requirements.lock

RUN TEMPOUP="$(mktemp)" \
    && curl -fsSL "https://raw.githubusercontent.com/tempoxyz/tempo/${TEMPOUP_COMMIT}/tempoup/tempoup" -o "$TEMPOUP" \
    && echo "${TEMPOUP_SHA256}  ${TEMPOUP}" | sha256sum -c - \
    && install -m 0755 "$TEMPOUP" /usr/local/bin/tempoup \
    && TEMPO_BIN_DIR=/root/.tempo/bin tempoup --install "$TEMPO_VERSION" \
    && rm -f "$TEMPOUP" /usr/local/bin/tempoup \
    && test -x /root/.tempo/bin/tempo \
    && /root/.tempo/bin/tempo --version

ENV TEMPO_BIN=/root/.tempo/bin/tempo

COPY . .

CMD ["bash", "start.sh"]
