# syntax=docker/dockerfile:1
FROM ghcr.io/astral-sh/uv:0.11.21 AS uv

FROM python:3.14-slim AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock .python-version README.md LICENSE ./
RUN uv sync --locked --no-dev --no-install-project
COPY src ./src
RUN uv sync --locked --no-dev --no-editable

FROM python:3.14-slim
# Take the distribution's security fixes that landed after the base image was built. Nothing is installed at run
# time, so pip leaves the image too.
RUN apt-get update \
    && apt-get upgrade --yes --no-install-recommends \
    && rm -rf /var/lib/apt/lists/* \
    && python -m pip uninstall --yes --quiet pip \
    && useradd --system --uid 10001 --home-dir /app kyc \
    && mkdir /data && chown kyc /data
WORKDIR /app
COPY --from=build /app/.venv /app/.venv
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    KYC_DB=/data/kyc.sqlite3
USER kyc
VOLUME /data
EXPOSE 8000
# The slim image has no curl or wget, so the health check uses the Python that is already there.
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2).status == 200 else 1)"]
ENTRYPOINT ["kyc-api"]
# Inside the container the API must listen on all interfaces to be reachable through the published port. Publish it
# on the host's loopback only: docker run -p 127.0.0.1:8000:8000 ...
CMD ["--host", "0.0.0.0", "--port", "8000"]
