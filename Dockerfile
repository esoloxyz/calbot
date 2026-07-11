FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python3-pip \
    python3-venv \
    curl \
    ca-certificates \
    gnupg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN python3 -m venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

RUN TEMPO_INSTALLER="$(mktemp)" \
    && curl -fsSL https://tempo.xyz/install -o "$TEMPO_INSTALLER" \
    && bash "$TEMPO_INSTALLER" \
    && test -x /root/.tempo/bin/tempo \
    && /root/.tempo/bin/tempo --version

ENV TEMPO_BIN=/root/.tempo/bin/tempo

COPY . .

CMD ["bash", "start.sh"]
