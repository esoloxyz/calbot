FROM python:3.12-slim-bookworm

WORKDIR /app

# Install curl (needed for Tempo install script) and ca-certs
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install Tempo CLI during build (baked into the image, no runtime download needed)
RUN curl -fsSL https://tempo.xyz/install | bash

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["bash", "start.sh"]
