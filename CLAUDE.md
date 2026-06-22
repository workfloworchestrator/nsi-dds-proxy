# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

NSI DDS Proxy is a REST API proxy for the Network Service Interface (NSI) Document Distribution Service. It fetches NML (Network Modeling Language) XML topology documents from an upstream DDS server, parses them, and exposes the data as JSON REST endpoints. Part of the ANA-GRAM project for federated network automation across research institutions.

## Commands

```bash
# Install dependencies
uv sync

# Run all tests
uv run pytest

# Run a single test
uv run pytest tests/test_routes.py::test_name

# Lint
uv run ruff check .

# Type check (strict mode enabled)
uv run mypy dds_proxy

# Run the application locally
dds-proxy
```

## Architecture

**FastAPI async application** with these layers:

- **`main.py`** — App entry point. `create_app()` factory builds the FastAPI instance, replaces the built-in OpenAPI/docs/ReDoc routes with custom ones that sit behind the auth dependency (FastAPI's defaults cannot be put behind a `Depends`), and includes the data routers. Lifespan creates the shared `httpx.AsyncClient` with mutual TLS for the upstream DDS, plus structlog logging setup and the unprotected health check
- **`config.py`** — Pydantic Settings loaded from env vars or `dds_proxy.env`
- **`auth.py`** — Reads identity headers set by the edge proxy. The OIDC branch reads `X-Auth-Request-Email` (identity) and `X-Auth-Request-Groups` (group authorisation via set intersection against `OIDC_REQUIRED_GROUPS`). The mTLS branch accepts requests carrying the configured `MTLS_HEADER` (set by `nsi-auth` after cert verification) and logs `X-Client-DN` for audit. `get_authenticated_user` is the FastAPI dependency applied to all data routes via `include_router(dependencies=...)`
- **`dds_client.py`** — Core logic: fetches DDS collection, filters for topology documents, decodes gzip+base64 content, parses NML XML with lxml namespace-aware XPath. Has an in-memory TTL cache. Four `fetch_*` functions each return a list of parsed Pydantic models
- **`models.py`** — Pydantic models (Topology, SwitchingService, ServiceTerminationPoint, ServiceDemarcationPoint) with camelCase alias generators for JSON serialization
- **`routers/`** — One thin router per endpoint, all return 502 on upstream DDS failures

**Endpoints**: `/topologies`, `/switching-services`, `/service-termination-points`, `/service-demarcation-points`, `/health`

## Key Design Decisions

- Fully async (httpx + FastAPI), stateless with no database
- Mutual TLS for DDS communication (client cert + custom CA bundle)
- `root_path` setting for serving behind a path-stripping reverse proxy (e.g. the ana-automation-ui portal)
- XML parsing uses 4 NML/DDS namespaces defined in `dds_client.py`
- All responses are full collections (no filtering/pagination)
- Authentication is performed at the edge proxy (Traefik on the dev cluster) and the proxy trusts the resulting identity headers. Two routes converge on the same backend:
  - Portal route via oauth2-proxy: Traefik chains `ana-automation-ui-strip-auth-headers` (zeros inbound `X-Auth-Request-*`, `X-Auth-Method`, `X-Client-DN`, `Authorization` so clients can't self-attest) → `ana-automation-ui-oauth2` ForwardAuth → URL rewrite. oauth2-proxy is configured with `set_xauthrequest = true` and `oidc_groups_claim = "eduperson_entitlement"`; `authResponseHeaders` forwards `X-Auth-Request-User/Email/Groups`.
  - mTLS route via `nsi-auth`: `RequireAndVerifyClientCert` at the TLS layer, then `nsi-pass-tls` + `nsi-dds-proxy-auth` middlewares pass the cert PEM to the validate sidecar, which returns `X-Auth-Method` + `X-Client-DN`.
- `/openapi.json`, `/docs`, and `/redoc` share the data endpoints' auth dependency: authenticated users in `OIDC_REQUIRED_GROUPS` get the UIs, unauthenticated requests get 401, and authenticated-but-out-of-group requests get 403. `/health` stays unauthenticated for k8s probes.
- `OIDC_REQUIRED_GROUPS` is `Annotated[list[str], NoDecode]` so its `field_validator` runs on the raw env string: comma-separated, single value, JSON array, and empty (`-> []`) all work. Without `NoDecode`, pydantic-settings JSON-parses `list[str]` env vars before the validator, so anything but a JSON array (including `""`) crashes at startup.
- pytest-asyncio with `asyncio_mode=auto`; tests mock the HTTP client via fixtures in `conftest.py`

## Code Style

- Ruff with Google-style docstrings, 120-char line limit
- mypy strict mode (disallow_untyped_defs, disallow_incomplete_defs)
