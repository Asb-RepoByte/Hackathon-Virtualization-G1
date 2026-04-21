FROM python:3.11-slim

WORKDIR /app

# System dependencies: SSH tools + Docker CLI (needed by python-on-whales for remote SSH connections)
RUN apt-get update && apt-get install -y --no-install-recommends \
    openssh-client \
    sshpass \
    curl \
    && curl -fsSL https://download.docker.com/linux/static/stable/x86_64/docker-27.5.1.tgz | \
       tar -xz --strip-components=1 -C /usr/local/bin docker/docker \
    && rm -rf /var/lib/apt/lists/*

# Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Application code
COPY . .

# Create logs directory
RUN mkdir -p logs

# Default command (overridden per service in docker-compose)
CMD ["python", "admin_server.py"]
