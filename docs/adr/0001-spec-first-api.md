# ADR 0001: Spec-first OpenAPI contract

Status: accepted

## Context
The HTTP API is consumed by the Next.js app and agents. Code-first generation lets the implementation define the contract by accident.

## Decision
api/openapi.yaml is the source of truth. Changes go spec → scripts/gen-api.sh → implementation. CI runs Spectral, oasdiff (breaking-change check), a generated-code sync check and Schemathesis conformance tests.

## Consequences
Contract diffs are small and easy to review; the frontend and backend cannot drift. Generated code is never hand-edited.
