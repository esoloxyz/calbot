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
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN python3 -m venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"

# Install Tempo CLI — print output so build logs show any failure
RUN curl -fsSL https://tempo.xyz/install | bash && \
    echo "Tempo install done" && \
    ls -la /root/.tempo/bin/ && \
    /root/.tempo/bin/tempo --version || echo "WARNING: tempo binary not working"

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["bash", "start.sh"]
