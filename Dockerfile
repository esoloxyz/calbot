FROM ubuntu:25.10@sha256:7cc5e35f6567ee8c66d2abb4aab0fd866669e6207c237c3a8f0947a5c7f17092 AS builder

ARG OTEL_EXPORTER_OTLP_ENDPOINT
ARG OTEL_EXPORTER_OTLP_TRACES_ENDPOINT
ARG TEMPOUP_COMMIT=96cec1ee6735834d1674f282ef317b708ec6de53
ARG TEMPOUP_SHA256=5a6e26630f804f226264f5da4553c3eb3cb7e15ec387c3392d7f6749422042d9
ARG TEMPO_VERSION=v1.4.3
ARG TEMPO_WALLET_VERSION=v0.6.7
ARG TEMPO_REQUEST_VERSION=v0.6.5

ENV DEBIAN_FRONTEND=noninteractive
ENV PIP_DISABLE_PIP_VERSION_CHECK=1
ENV PIP_NO_INPUT=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    gnupg \
    python3 \
    python3-pip \
    python3-venv \
    && rm -rf /var/lib/apt/lists/*

RUN python3 -m venv /opt/venv
WORKDIR /build
COPY requirements.lock .
RUN /opt/venv/bin/python -m pip install --no-cache-dir --only-binary=:all: --require-hashes -r requirements.lock

# tempoup is source-pinned and checksum-verified. It verifies the versioned Tempo
# release before installing it; build-only download and GPG tools stay out of the
# runtime image. Hosted builders can inject OTLP endpoints for their own
# tracing; Tempo must not inherit those platform-specific Unix sockets.
RUN unset OTEL_EXPORTER_OTLP_ENDPOINT OTEL_EXPORTER_OTLP_TRACES_ENDPOINT \
    OTEL_EXPORTER_OTLP_PROTOCOL OTEL_EXPORTER_OTLP_TRACES_PROTOCOL OTEL_TRACES_EXPORTER \
    && TEMPOUP="$(mktemp)" \
    && curl -fsSL "https://raw.githubusercontent.com/tempoxyz/tempo/${TEMPOUP_COMMIT}/tempoup/tempoup" -o "$TEMPOUP" \
    && echo "${TEMPOUP_SHA256}  ${TEMPOUP}" | sha256sum -c - \
    && install -m 0755 "$TEMPOUP" /usr/local/bin/tempoup \
    && TEMPO_BIN_DIR=/opt/tempo/bin tempoup --install "$TEMPO_VERSION" \
    && rm -f "$TEMPOUP" /usr/local/bin/tempoup \
    && test -x /opt/tempo/bin/tempo \
    && /opt/tempo/bin/tempo --version \
    && TEMPO_HOME=/opt/tempo /opt/tempo/bin/tempo add wallet "$TEMPO_WALLET_VERSION" \
    && TEMPO_HOME=/opt/tempo /opt/tempo/bin/tempo add request "$TEMPO_REQUEST_VERSION" \
    && /opt/tempo/bin/tempo-wallet --version \
    && /opt/tempo/bin/tempo-request --version

FROM ubuntu:25.10@sha256:7cc5e35f6567ee8c66d2abb4aab0fd866669e6207c237c3a8f0947a5c7f17092

ARG OTEL_EXPORTER_OTLP_ENDPOINT
ARG OTEL_EXPORTER_OTLP_TRACES_ENDPOINT

ENV DEBIAN_FRONTEND=noninteractive
ENV HOME=/home/calbot
ENV PATH=/opt/venv/bin:/opt/tempo/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV TEMPO_BIN=/opt/tempo/bin/tempo
ENV TEMPO_HOME=/opt/tempo

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    python3 \
    sqlite3 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 calbot \
    && useradd --uid 10001 --gid 10001 --create-home --home-dir /home/calbot --shell /usr/sbin/nologin calbot \
    && install -d -o root -g root -m 0755 /opt/tempo/bin \
    && install -d -o calbot -g calbot -m 0700 /home/calbot/.tempo /home/calbot/.tempo/wallet \
    && install -d -o root -g root -m 0755 /app

COPY --from=builder /opt/venv /opt/venv
COPY --from=builder --chown=root:root --chmod=0555 /opt/tempo/bin/tempo /opt/tempo/bin/tempo
COPY --from=builder --chown=root:root --chmod=0555 /opt/tempo/bin/tempo-wallet /opt/tempo/bin/tempo-wallet
COPY --from=builder --chown=root:root --chmod=0555 /opt/tempo/bin/tempo-request /opt/tempo/bin/tempo-request

WORKDIR /app
COPY --chmod=0444 *.py ./
COPY --chmod=0555 start.sh ./

RUN unset OTEL_EXPORTER_OTLP_ENDPOINT OTEL_EXPORTER_OTLP_TRACES_ENDPOINT \
    OTEL_EXPORTER_OTLP_PROTOCOL OTEL_EXPORTER_OTLP_TRACES_PROTOCOL OTEL_TRACES_EXPORTER \
    && sqlite3 --version \
    && python3 -c "import sqlite3; print(sqlite3.sqlite_version)" \
    && python3 -m py_compile /app/process_guard.py \
    && python3 /app/process_guard.py "$(python3 -c 'from tempo_process import MAX_TEMPO_REQUEST_DATA_MEMORY_BYTES; print(MAX_TEMPO_REQUEST_DATA_MEMORY_BYTES)')" /opt/tempo/bin/tempo-request --help >/dev/null \
    && /opt/tempo/bin/tempo --version \
    && /opt/tempo/bin/tempo-wallet --version \
    && /opt/tempo/bin/tempo-request --version

USER calbot:calbot

CMD ["bash", "/app/start.sh"]
