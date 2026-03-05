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

import httpx
import structlog
from fastapi import FastAPI
from ssl import create_default_context

from dds_proxy.config import get_settings
from dds_proxy.routers import topologies, switching_services, stps, sdps


# ---------------------------------------------------------------------------
# structlog configuration
# ---------------------------------------------------------------------------

def configure_logging() -> None:
    import logging
    logging.basicConfig(format="%(message)s", level=logging.DEBUG)

    structlog.configure(
        processors=[
            structlog.stdlib.add_log_level,
            structlog.stdlib.add_logger_name,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.dev.ConsoleRenderer(),   # human-friendly coloured output
        ],
        wrapper_class=structlog.make_filtering_bound_logger(10),  # DEBUG and above
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
    )


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    log = structlog.get_logger(__name__)

    settings = get_settings()
    log.info(
        "app.starting",
        dds_base_url=settings.dds_base_url,
        cache_ttl=settings.cache_ttl_seconds,
        cert=settings.dds_client_cert,
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
    import uvicorn
    uvicorn.run("dds_proxy.main:app", host="0.0.0.0", port=8000, reload=False)