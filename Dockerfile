FROM node:20-slim

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    curl \
    wget \
    ca-certificates \
    build-essential \
    cmake \
    make \
    python3 \
    python3-pip \
    jq \
    unzip \
    && rm -rf /var/lib/apt/lists/*

# Install gh CLI
RUN curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
    | dd of=/usr/share/keyrings/githubcli-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
    | tee /etc/apt/sources.list.d/github-cli.list > /dev/null \
    && apt-get update && apt-get install -y gh \
    && rm -rf /var/lib/apt/lists/*

# Install Claude CLI + Agent SDK
RUN npm install -g @anthropic-ai/claude-code
RUN pip install --break-system-packages claude-agent-sdk

# Copy the klaus-kode package and MCP config
COPY klaus_kode/ /app/klaus_kode/
COPY .mcp.json /workspace/.mcp.json

# Create non-root user (Claude CLI refuses --dangerously-skip-permissions as root)
RUN useradd -m -s /bin/bash claude && \
    mkdir -p /workspace && chown claude:claude /workspace && \
    mkdir -p /workspace/logs && chown claude:claude /workspace/logs && \
    mkdir -p /home/claude/.claude && chown claude:claude /home/claude/.claude && \
    echo '{"hasCompletedOnboarding": true}' > /home/claude/.claude.json && \
    chown claude:claude /home/claude/.claude.json

ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

USER claude
WORKDIR /workspace

ENTRYPOINT ["python3", "-m", "klaus_kode.cli"]
