"""Runs INSIDE a hardened writer container (sandbox/tests/docker/isolation.sh).

Prints one JSON object of named checks (true = the isolation property holds) and exits 1 if
any check is false. Stdlib only; makes only the network attempts it is testing.
"""

from __future__ import annotations

import http.client
import json
import os
import socket
import sys
import tempfile
from pathlib import Path

PROXY_HOST, PROXY_PORT = "egress-proxy", 3128
LLM_PROXY = os.environ.get("PROBE_LLM_PROXY", "llm-proxy:8080")
COORDINATOR = os.environ.get("PROBE_COORDINATOR", "coordinator:8100")
SECRET_ENV = (
    "ANTHROPIC_API_KEY",
    "GOOGLE_APPLICATION_CREDENTIALS",
    "CLOUDSDK_AUTH_ACCESS_TOKEN",
    "CLOUDSDK_CONFIG",
    "LLM_PROXY_SECRET",
    "GH_TOKEN",
    "GITHUB_TOKEN",
)
SECRET_NAMES = ("application_default_credentials.json", "credentials.db", "local.env", ".env")
SKIP_DIRS = ("/proc", "/sys", "/dev")


def direct_blocked(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=4):
            return False
    except OSError:
        return True


def proxy_connect_refused(target: str) -> bool:
    """CONNECT through the egress proxy must not return 200."""
    try:
        conn = http.client.HTTPConnection(PROXY_HOST, PROXY_PORT, timeout=8)
        conn.request("CONNECT", target)
        status = conn.getresponse().status
        conn.close()
    except OSError:
        return True
    return status != 200


def proxy_get_status(url: str) -> int | None:
    try:
        conn = http.client.HTTPConnection(PROXY_HOST, PROXY_PORT, timeout=8)
        conn.request("GET", url)
        status = conn.getresponse().status
        conn.close()
    except OSError:
        return None
    return status


def proxy_get_refused(url: str) -> bool:
    status = proxy_get_status(url)
    return status is None or status >= 400


def proxy_allows(url: str) -> bool:
    """Squid lets the request through: the answer comes from the service, not a 403 denial."""
    status = proxy_get_status(url)
    return status is not None and status != 403


def writable(path: str) -> bool:
    probe = Path(path) / ".probe-write"
    try:
        probe.write_text("x")
        probe.unlink()
    except OSError:
        return False
    return True


def secret_files() -> list[str]:
    found: list[str] = []
    for root, dirs, files in os.walk("/", onerror=lambda _e: None):
        if root.startswith(SKIP_DIRS):
            dirs[:] = []
            continue
        if root.endswith("/.config/jobradar") or root.endswith("/.config/gcloud"):
            found.append(root)
        found += [f"{root}/{f}" for f in files if f in SECRET_NAMES]
    return found


def llm_status(token: str | None, max_tokens: int = 100) -> int:
    host, _, port = LLM_PROXY.partition(":")
    body = json.dumps(
        {"model": "claude-haiku-5-5", "max_tokens": max_tokens, "messages": []}
    ).encode()
    headers = {"content-type": "application/json", "anthropic-version": "2023-06-01"}
    if token:
        headers["authorization"] = f"Bearer {token}"
    conn = http.client.HTTPConnection(host, int(port or 80), timeout=10)
    conn.request("POST", "/v1/messages", body=body, headers=headers)
    status = conn.getresponse().status
    conn.close()
    return status


def usage_status(token: str) -> int:
    host, _, port = LLM_PROXY.partition(":")
    conn = http.client.HTTPConnection(host, int(port or 80), timeout=10)
    conn.request("GET", "/v1/usage", headers={"authorization": f"Bearer {token}"})
    status = conn.getresponse().status
    conn.close()
    return status


def token_checks() -> int:
    """Tokens are minted on the host and passed in by env NAME (PROBE_TOKEN_*)."""
    checks = {
        "missing_token_401": llm_status(None) == 401,
        "expired_token_401": llm_status(os.environ["PROBE_TOKEN_EXPIRED"]) == 401,
        "forged_token_401": llm_status(os.environ["PROBE_TOKEN_FORGED"]) == 401,
        "jrt1_token_401": llm_status(os.environ["PROBE_TOKEN_JRT1"]) == 401,
        "over_budget_token_403": llm_status(os.environ["PROBE_TOKEN_SMALL"], 1000) == 403,
        # Valid token: passes auth, then fails upstream (the test proxy has a dead upstream).
        "valid_token_reaches_upstream_502": llm_status(os.environ["PROBE_TOKEN_VALID"]) == 502,
        "usage_endpoint_200": usage_status(os.environ["PROBE_TOKEN_VALID"]) == 200,
        "usage_endpoint_jrt1_401": usage_status(os.environ["PROBE_TOKEN_JRT1"]) == 401,
    }
    print(json.dumps(checks, sort_keys=True))
    return 0 if all(checks.values()) else 1


def main() -> int:
    if sys.argv[1:] == ["tokens"]:
        return token_checks()
    llm_host, _, llm_port = LLM_PROXY.partition(":")
    coord_host, _, coord_port = COORDINATOR.partition(":")
    checks = {
        "non_root": Path("/proc/self").stat().st_uid != 0,
        "direct_example_com_blocked": direct_blocked("example.com", 443),
        "direct_metadata_blocked": direct_blocked("169.254.169.254", 80),
        "proxy_example_com_refused": proxy_connect_refused("example.com:443"),
        "proxy_metadata_ip_refused": proxy_get_refused(
            "http://169.254.169.254/computeMetadata/v1/"
        ),
        "proxy_metadata_name_refused": proxy_get_refused(
            "http://metadata.google.internal/computeMetadata/v1/"
        ),
        "proxy_private_range_refused": proxy_connect_refused("10.0.0.1:443"),
        "llm_proxy_reachable_directly": not direct_blocked(llm_host, int(llm_port or 80)),
        "coordinator_reachable_directly": not direct_blocked(coord_host, int(coord_port or 80)),
        "proxy_coordinator_allowed": proxy_allows(f"http://{COORDINATOR}/"),
        "proxy_coordinator_other_port_refused": proxy_connect_refused(f"{coord_host}:8080"),
        "proxy_llm_proxy_other_port_refused": proxy_connect_refused("llm-proxy:8100"),
        "no_secret_env": not [k for k in SECRET_ENV if k in os.environ],
        "no_secret_files": not secret_files(),
        "no_docker_socket": not Path("/var/run/docker.sock").exists(),
        "rootfs_readonly": not any(writable(p) for p in ("/", "/usr", "/etc", "/opt", "/home")),
        "workspace_writable": writable("/workspace"),
        "tmp_writable": writable(tempfile.gettempdir()),
    }
    print(json.dumps(checks, sort_keys=True))
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
