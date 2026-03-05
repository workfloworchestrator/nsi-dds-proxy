# Copyright 2026 SURF.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
from contextlib import asynccontextmanager
from ssl import create_default_context
from typing import AsyncIterator

import httpx
import structlog
from fastapi import FastAPI

from dds_proxy.config import get_settings
from dds_proxy.routers import sdps, stps, switching_services, topologies

# ---------------------------------------------------------------------------
# structlog configuration
# ---------------------------------------------------------------------------


def configure_logging() -> None:
    """Configure logging."""
    import logging

    settings = get_settings()
    numeric_level = getattr(logging, settings.log_level.upper(), logging.INFO)

    # Shared pre-processing steps applied to every log record regardless of origin.
    shared_processors: list[structlog.types.Processor] = [
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
    ]

    # structlog-native loggers: run shared processors, then hand the event dict
    # off to ProcessorFormatter via wrap_for_formatter so it can render it with
    # the same formatter used for stdlib (uvicorn) records.
    structlog.configure(
        processors=shared_processors
        + [
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(numeric_level),
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
    )

    # ProcessorFormatter renders both structlog-native records (already processed
    # by shared_processors above) and foreign stdlib records (uvicorn, etc., which
    # get shared_processors applied via foreign_pre_chain).
    # remove_processors_meta strips internal keys (_record, _from_structlog) so
    # they never appear in the rendered output.
    formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.dev.ConsoleRenderer(),
        ],
        foreign_pre_chain=shared_processors,
    )

    handler = logging.StreamHandler()
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(numeric_level)

    # Tell uvicorn not to touch its own log config so our handler wins.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uvi_logger = logging.getLogger(name)
        uvi_logger.handlers.clear()
        uvi_logger.propagate = True


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Context manager for lifespan.

    The lifespan parameter defines code that runs when the application
    starts up and shuts down, using a single async context manager.
    """
    configure_logging()
    log = structlog.get_logger(__name__)

    settings = get_settings()
    log.info(
        "app.starting",
        dds_base_url=settings.dds_base_url,
        cache_ttl=settings.cache_ttl_seconds,
        cert=settings.dds_client_cert,
        log_level=settings.log_level.upper(),
    )

    ssl_context = create_default_context()
    ssl_context.load_cert_chain(
        certfile=settings.dds_client_cert,
        keyfile=settings.dds_client_key,
    )

    app.state.http_client = httpx.AsyncClient(
        verify=ssl_context,
        timeout=settings.http_timeout_seconds,
    )
    log.info("app.http_client.ready")
    yield

    await app.state.http_client.aclose()
    log.info("app.shutdown")


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(
    title="NSI DDS Proxy",
    description=(
        "REST proxy for the NSI Document Distribution Service. "
        "Returns topologies, switching services, STPs, and SDPs "
        "extracted from DDS topology documents."
    ),
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(topologies.router)
app.include_router(switching_services.router)
app.include_router(stps.router)
app.include_router(sdps.router)


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------


@app.get("/health", tags=["meta"])
async def health() -> dict:
    """Quick liveness check — useful for load balancers and k8s probes."""
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


def run() -> None:
    """Entry point to run the app."""
    import uvicorn

    uvicorn.run("dds_proxy.main:app", host="0.0.0.0", port=8000, reload=False, log_config=None)
