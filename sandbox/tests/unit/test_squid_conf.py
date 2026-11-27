"""Static checks on the egress allowlist: squid evaluates http_access in order, so assert it."""

from pathlib import Path

CONF_DIR = Path(__file__).resolve().parents[3] / "scripts" / "lib" / "egress-proxy"
INCLUDE = "include /etc/squid/endpoint.conf"
INTERNAL_ALLOWS = [
    "http_access allow llm_proxy_svc llm_proxy_port",
    "http_access allow coordinator_svc coordinator_port",
]


def _rules(name: str) -> list[str]:
    lines = (CONF_DIR / name).read_text().splitlines()
    return [s for s in (line.strip() for line in lines) if s.startswith(("http_access", "include"))]


def test_denies_come_before_any_external_allow() -> None:
    rules = _rules("squid.conf")
    assert rules[-1] == "http_access deny all"
    idx = {r: i for i, r in enumerate(rules)}
    for deny in (
        "http_access deny !from_sandbox",
        "http_access deny metadata_name",
        "http_access deny metadata_dst",
        "http_access deny private_dst",
        "http_access deny !CONNECT",
        "http_access deny !SSL_ports",
    ):
        assert idx[deny] < idx[INCLUDE]
    for allow in INTERNAL_ALLOWS:
        assert idx["http_access deny metadata_dst"] < idx[allow]
        assert idx[allow] < idx["http_access deny private_dst"]
    assert [r for r in rules if r.startswith("http_access allow")] == INTERNAL_ALLOWS


def test_internal_services_pinned_to_their_own_port() -> None:
    """llm-proxy only on 8080 and coordinator only on 8100 (no cross-pairing)."""
    acls = {
        tuple(line.split()[1:3]): line.split()[3:]
        for line in (CONF_DIR / "squid.conf").read_text().splitlines()
        if line.startswith("acl ")
    }
    assert acls[("llm_proxy_svc", "dstdomain")] == ["llm-proxy"]
    assert acls[("llm_proxy_port", "port")] == ["8080"]
    assert acls[("coordinator_svc", "dstdomain")] == ["coordinator"]
    assert acls[("coordinator_port", "port")] == ["8100"]


def test_metadata_ip_is_covered() -> None:
    assert "acl metadata_dst dst 169.254.0.0/16" in (CONF_DIR / "squid.conf").read_text()


def test_endpoint_files() -> None:
    assert _rules("endpoint-vertex.conf") == ["http_access allow CONNECT SSL_ports model_endpoint"]
    assert "dstdomain .googleapis.com" in (CONF_DIR / "endpoint-vertex.conf").read_text()
    assert _rules("endpoint-llm-proxy.conf") == []
