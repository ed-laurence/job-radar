# ADR 0004: In-app agents call domain services directly, not via the MCP connector

Status: accepted

## Context
The MCP connector is not available on Vertex AI.

## Decision
In-app agents (triage, research) use the Anthropic SDK tool runner with tools that call domain services directly. The app doesn't use MCP anywhere (ADR-0009).

## Consequences
Shorter request path, same authz and guards.
