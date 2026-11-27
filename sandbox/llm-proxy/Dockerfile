# llm-proxy: forwards the Anthropic Messages API for sandboxed agents and injects the real key
# from its own environment. Python stdlib only, so there is nothing to install.
# Build: docker compose --profile agents build llm-proxy
ARG PYTHON_IMAGE=python:3.12.16-slim-bookworm@sha256:44cf1793efa7c7a476bb0f2cff81443f4a072c90c1cb127e2924933daa3e8c64
FROM ${PYTHON_IMAGE}
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 LLM_PROXY_LISTEN=0.0.0.0:8080
WORKDIR /app
COPY llm_proxy/ ./llm_proxy/
RUN python -m compileall -q llm_proxy && useradd --uid 10002 --no-create-home --shell /usr/sbin/nologin llmproxy
USER 10002:10002
EXPOSE 8080
HEALTHCHECK --interval=5s --timeout=3s --retries=10 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=2)"]
ENTRYPOINT ["python", "-m", "llm_proxy"]
