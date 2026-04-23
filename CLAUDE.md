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

- **`main.py`** — App entry point, lifespan management (creates shared `httpx.AsyncClient` with mutual TLS), structured logging setup via structlog, health check endpoint
- **`config.py`** — Pydantic Settings loaded from env vars or `dds_proxy.env`
- **`dds_client.py`** — Core logic: fetches DDS collection, filters for topology documents, decodes gzip+base64 content, parses NML XML with lxml namespace-aware XPath. Has an in-memory TTL cache. Four `fetch_*` functions each return a list of parsed Pydantic models
- **`models.py`** — Pydantic models (Topology, SwitchingService, ServiceTerminationPoint, ServiceDemarcationPoint) with camelCase alias generators for JSON serialization
- **`routers/`** — One thin router per endpoint, all return 502 on upstream DDS failures

**Endpoints**: `/topologies`, `/switching-services`, `/service-termination-points`, `/service-demarcation-points`, `/health`

## Key Design Decisions

- Fully async (httpx + FastAPI), stateless with no database
- Mutual TLS for DDS communication (client cert + custom CA bundle)
- `root_path` setting for serving behind a path-stripping reverse proxy (e.g. the nsi-mgmt-info portal)
- XML parsing uses 4 NML/DDS namespaces defined in `dds_client.py`
- All responses are full collections (no filtering/pagination)
- pytest-asyncio with `asyncio_mode=auto`; tests mock the HTTP client via fixtures in `conftest.py`

## Code Style

- Ruff with Google-style docstrings, 120-char line limit
- mypy strict mode (disallow_untyped_defs, disallow_incomplete_defs)
