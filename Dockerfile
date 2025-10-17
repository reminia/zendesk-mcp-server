FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_ROOT_USER_ACTION=ignore

WORKDIR /app

# Install minimal OS dependencies and create an unprivileged user
RUN apt-get update \
    && apt-get install --no-install-recommends -y ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system appuser \
    && useradd --system --gid appuser --shell /usr/sbin/nologin appuser

COPY pyproject.toml requirements.lock README.md /app/

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.lock \
    && rm -rf /root/.cache

COPY src /app/src

RUN pip install --no-cache-dir --no-deps .

# Drop privileges for the runtime container
USER appuser

# Default command – expects Zendesk credentials via environment variables or an --env-file
CMD ["zendesk"]
