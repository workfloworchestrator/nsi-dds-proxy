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
import importlib
import logging
import platform
import ssl
from contextlib import asynccontextmanager
from typing import AsyncIterator

import httpx
import structlog
from fastapi import Depends, FastAPI

from dds_proxy.auth import OIDCProvider, get_authenticated_user
from dds_proxy.config import settings
from dds_proxy.routers import sdps, stps, switching_services, topologies

# ---------------------------------------------------------------------------
# structlog configuration
# ---------------------------------------------------------------------------


def configure_logging() -> None:
    """Configure structlog and the stdlib root logger to share a single output pipeline.

    Both structlog-native loggers and foreign stdlib loggers (e.g. uvicorn) are
    routed through a structlog ``ProcessorFormatter``, ensuring consistent
    formatting across all log sources. The log level is read from settings.

    Access log records for ``/health`` are suppressed entirely via a
    ``logging.Filter`` attached to the ``uvicorn.access`` logger so that
    frequent liveness probes from load balancers and k8s do not appear in the
    logs at all.
    """
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

    # Suppress /health access log records entirely so that frequent liveness
    # probes from load balancers and k8s do not appear in the logs at all.
    class _SuppressHealthCheck(logging.Filter):
        def filter(self, record: logging.LogRecord) -> bool:
            return " /health " not in record.getMessage()

    access_logger = logging.getLogger("uvicorn.access")
    access_logger.filters.clear()
    access_logger.addFilter(_SuppressHealthCheck())


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Manage application startup and shutdown.

    On startup: configures logging, builds an SSL context from the configured
    client certificate and key, and creates a shared ``httpx.AsyncClient``
    attached to ``app.state.http_client``.
    On shutdown: closes the HTTP client gracefully.
    """
    log = structlog.get_logger(__name__)

    application_version = importlib.metadata.version("dds-proxy")
    python_version = platform.python_version()
    python_implementation = platform.python_implementation()
    node = platform.node()
    log.info(
        "Starting dds-proxy %s using Python %s (%s) on %s",
        application_version,
        python_version,
        python_implementation,
        node,
        application_version=application_version,
        python_version=python_version,
        python_implementation=python_implementation,
        node=node,
    )

    log.info(
        "Application settings",
        dds_base_url=settings.dds_base_url,
        cache_ttl=settings.cache_ttl_seconds,
        http_timeout_seconds=settings.http_timeout_seconds,
        dds_client_cert=str(settings.dds_client_cert),
        dds_client_key=str(settings.dds_client_key),
        dds_ca_bundle=str(settings.dds_ca_bundle),
        host=settings.dds_proxy_host,
        port=settings.dds_proxy_port,
        log_level=settings.log_level.upper(),
        auth_enabled=settings.auth_enabled,
        mtls_header=settings.mtls_header,
    )

    ssl_context: ssl.SSLContext | str | bool
    if (
        settings.dds_client_cert
        and settings.dds_client_key
        and settings.dds_client_cert.is_file()
        and settings.dds_client_key.is_file()
    ):
        ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ssl_context.verify_mode = ssl.CERT_REQUIRED
        ssl_context.check_hostname = True
        if settings.dds_ca_bundle and settings.dds_ca_bundle.is_file():
            ssl_context.load_verify_locations(cafile=settings.dds_ca_bundle)
        else:
            ssl_context.load_default_certs()
        ssl_context.load_cert_chain(
            certfile=settings.dds_client_cert,
            keyfile=settings.dds_client_key,
        )
    else:
        ssl_context = False
    app.state.http_client = httpx.AsyncClient(
        verify=ssl_context,
        timeout=settings.http_timeout_seconds,
    )

    if settings.auth_enabled and settings.oidc_issuer:
        app.state.oidc_http_client = httpx.AsyncClient()
        jwks_uri = settings.oidc_jwks_uri
        userinfo_uri = settings.oidc_userinfo_uri

        if not jwks_uri or not userinfo_uri:
            oidc_config_url = f"{settings.oidc_issuer.rstrip('/')}/.well-known/openid-configuration"
            log.info("Discovering OIDC configuration", url=oidc_config_url)
            resp = await app.state.oidc_http_client.get(oidc_config_url)
            resp.raise_for_status()
            oidc_config = resp.json()
            jwks_uri = jwks_uri or oidc_config.get("jwks_uri", "")
            userinfo_uri = userinfo_uri or oidc_config.get("userinfo_endpoint", "")

        if not jwks_uri or not userinfo_uri:
            log.error(
                "OIDC configuration incomplete",
                jwks_uri=bool(jwks_uri),
                userinfo_uri=bool(userinfo_uri),
            )
            raise SystemExit("OIDC requires both jwks_uri and userinfo_endpoint")

        app.state.oidc_provider = OIDCProvider(
            jwks_uri=jwks_uri,
            userinfo_uri=userinfo_uri,
            http_client=app.state.oidc_http_client,
            cache_lifespan=settings.oidc_jwks_cache_lifespan,
            userinfo_cache_ttl=settings.oidc_userinfo_cache_ttl,
        )
        log.info(
            "OIDC authentication enabled",
            issuer=settings.oidc_issuer,
            audience=settings.oidc_audience,
            jwks_uri=jwks_uri,
            userinfo_uri=userinfo_uri,
        )
    else:
        app.state.oidc_provider = None
        app.state.oidc_http_client = None

    if settings.auth_enabled:
        methods = []
        if settings.oidc_issuer:
            methods.append("OIDC")
        if settings.mtls_header:
            methods.append(f"mTLS (header: {settings.mtls_header})")
        log.info("Authentication enabled", methods=methods)
    else:
        log.info("Authentication disabled")

    yield

    await app.state.http_client.aclose()
    if app.state.oidc_http_client:
        await app.state.oidc_http_client.aclose()
    log.info("Application shutdown")


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
    version=importlib.metadata.version("dds-proxy"),
    lifespan=lifespan,
    root_path=settings.root_path,
)

_auth_deps = [Depends(get_authenticated_user)]
app.include_router(topologies.router, dependencies=_auth_deps)
app.include_router(switching_services.router, dependencies=_auth_deps)
app.include_router(stps.router, dependencies=_auth_deps)
app.include_router(sdps.router, dependencies=_auth_deps)


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
    """Start the uvicorn server using host and port from settings."""
    import uvicorn

    # Configure logging before uvicorn starts so that the _SuppressHealthCheck
    # filter is attached to uvicorn.access before uvicorn initialises its
    # loggers.
    configure_logging()

    uvicorn.run(app, host=settings.dds_proxy_host, port=settings.dds_proxy_port, reload=False, log_config=None)
