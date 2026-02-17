FROM python:3.11-slim

# Install Java 17 (for Lavalink), supervisor, and build deps
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    openjdk-17-jre-headless \
    supervisor \
    curl \
    libffi-dev \
    python3-dev \
    gcc \
    libsodium-dev && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Download Lavalink
RUN curl -L -o Lavalink.jar https://github.com/lavalink-devs/Lavalink/releases/download/4.0.8/Lavalink.jar

# Copy Lavalink config
COPY application.yml .

# Install Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy bot
COPY bot.py .

# Copy supervisor config
COPY supervisord.conf /etc/supervisor/conf.d/supervisord.conf

# Expose health check port and Lavalink port
EXPOSE 8000 2333

CMD ["supervisord", "-c", "/etc/supervisor/conf.d/supervisord.conf"]
