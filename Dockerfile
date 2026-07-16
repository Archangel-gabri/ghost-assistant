FROM python:3.11-slim

LABEL maintainer="Danya Kubrak <archangel-gabri@users.noreply.github.com>"
LABEL description="Ghost — voice + screen assistant — Super-fast AI session helper"

ENV PYTHONUNBUFFERED=1
ENV DISPLAY=:0

WORKDIR /app

# System dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    libxkbcommon0 \
    libdbus-1-3 \
    libssl3 \
    libpulse0 \
    libxcb-icccm4 \
    libxcb-image0 \
    libxcb-keysyms1 \
    libxcb-randr0 \
    libxcb-render0 \
    libxcb-shape0 \
    libxcb-xfixes0 \
    libxcb-xinerama0 \
    libxcb-xkb1 \
    libxkbcommon-x11-0 \
    && rm -rf /var/lib/apt/lists/*

# Copy project
COPY . .

# Install Python dependencies
RUN pip install --no-cache-dir --break-system-packages -e .

# Create non-root user
RUN useradd -m -s /bin/bash ghost

EXPOSE 9999

ENTRYPOINT ["ghost"]
CMD ["--config", "config-fast.yaml"]
