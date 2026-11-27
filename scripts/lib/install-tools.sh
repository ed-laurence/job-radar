#!/usr/bin/env bash
# Install the scanner CLIs that scripts/check.sh uses when present (Linux x86_64/arm64 only).
# Used by .devcontainer (postCreateCommand) and the CI workflows, so both run the same versions.
#
#   scripts/lib/install-tools.sh [--bin-dir DIR]     default DIR: /usr/local/bin
#
# Every download is verified against the release's checksums file. Python tools go through
# `uv tool install` at pinned versions. Not for Windows dev machines: there, missing tools SKIP.
set -euo pipefail
JR_SCRIPT=install-tools
# shellcheck source=common.sh
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

GITLEAKS_VERSION=8.30.0
CONFTEST_VERSION=0.71.1
OASDIFF_VERSION=1.37.0
CHECKOV_VERSION=3.4.3
SEMGREP_VERSION=1.184.0

BIN_DIR=/usr/local/bin
[[ "${1:-}" == --bin-dir ]] && BIN_DIR="${2:?--bin-dir needs a path}"
mkdir -p "$BIN_DIR"

[[ "$(uname -s)" == Linux ]] || die "Linux only; on other systems install the tools yourself or let check.sh SKIP them"
case "$(uname -m)" in
  x86_64|amd64) arch=amd64 ;;
  aarch64|arm64) arch=arm64 ;;
  *) die "unsupported architecture: $(uname -m)" ;;
esac

tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT

fetch_verified() { # url checksums_url asset_name
  curl -fsSL --retry 3 -o "$tmp/$3" "$1"
  curl -fsSL --retry 3 -o "$tmp/sums.txt" "$2"
  (cd "$tmp" && grep -E "[[:space:]]\*?$3\$" sums.txt | sha256sum -c -) >&2 || die "checksum mismatch for $3"
}

# gitleaks
if ! have gitleaks || ! gitleaks version 2>/dev/null | grep -q "$GITLEAKS_VERSION"; then
  a=x64; [[ $arch == arm64 ]] && a=arm64
  asset="gitleaks_${GITLEAKS_VERSION}_linux_${a}.tar.gz"
  base="https://github.com/gitleaks/gitleaks/releases/download/v${GITLEAKS_VERSION}"
  fetch_verified "$base/$asset" "$base/gitleaks_${GITLEAKS_VERSION}_checksums.txt" "$asset"
  tar -xzf "$tmp/$asset" -C "$tmp" gitleaks && install -m 0755 "$tmp/gitleaks" "$BIN_DIR/gitleaks"
  ok "gitleaks $GITLEAKS_VERSION"
fi

# conftest
if ! have conftest; then
  a=x86_64; [[ $arch == arm64 ]] && a=arm64
  asset="conftest_${CONFTEST_VERSION}_Linux_${a}.tar.gz"
  base="https://github.com/open-policy-agent/conftest/releases/download/v${CONFTEST_VERSION}"
  fetch_verified "$base/$asset" "$base/checksums.txt" "$asset"
  tar -xzf "$tmp/$asset" -C "$tmp" conftest && install -m 0755 "$tmp/conftest" "$BIN_DIR/conftest"
  ok "conftest $CONFTEST_VERSION"
fi

# oasdiff
if ! have oasdiff; then
  asset="oasdiff_${OASDIFF_VERSION}_linux_${arch}.tar.gz"
  base="https://github.com/oasdiff/oasdiff/releases/download/v${OASDIFF_VERSION}"
  fetch_verified "$base/$asset" "$base/checksums.txt" "$asset"
  tar -xzf "$tmp/$asset" -C "$tmp" oasdiff && install -m 0755 "$tmp/oasdiff" "$BIN_DIR/oasdiff"
  ok "oasdiff $OASDIFF_VERSION"
fi

# Python-based scanners
if have uv; then
  have checkov || uv tool install --quiet "checkov==${CHECKOV_VERSION}"
  have semgrep || uv tool install --quiet "semgrep==${SEMGREP_VERSION}"
  ok "checkov $CHECKOV_VERSION, semgrep $SEMGREP_VERSION (uv tool; ensure \$(uv tool dir --bin) is on PATH)"
else
  warn "uv not found: checkov and semgrep not installed"
fi
