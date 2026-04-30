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
"""Tests for root_path configuration.

Verifies that the FastAPI app respects the ROOT_PATH setting so that
Swagger UI can find the OpenAPI spec when served behind a reverse proxy
with a path prefix (e.g. /dds-proxy).
"""

from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from dds_proxy.config import Settings
from tests.conftest import SIMPLE_COLLECTION


def make_app(root_path: str) -> FastAPI:
    """Create a fresh FastAPI app with the given root_path and the dds-proxy routes."""
    from dds_proxy.main import health, lifespan
    from dds_proxy.routers import sdps, stps, switching_services, topologies

    new_app = FastAPI(root_path=root_path, lifespan=lifespan)
    new_app.include_router(topologies.router)
    new_app.include_router(switching_services.router)
    new_app.include_router(stps.router)
    new_app.include_router(sdps.router)
    new_app.get("/health", tags=["meta"])(health)
    return new_app


def make_test_client(application: FastAPI) -> TestClient:
    """Create a TestClient with a mocked HTTP backend."""
    mock_http = AsyncMock()
    mock_response = AsyncMock()
    mock_response.content = SIMPLE_COLLECTION
    mock_response.raise_for_status = lambda: None
    mock_http.get = AsyncMock(return_value=mock_response)

    client = TestClient(application, raise_server_exceptions=True)
    client.__enter__()
    application.state.http_client = mock_http
    return client


class TestRootPathConfig:
    def test_default_root_path_is_empty(self):
        settings = Settings.model_construct()
        assert settings.root_path == ""

    def test_root_path_from_env(self, monkeypatch):
        monkeypatch.setenv("ROOT_PATH", "/dds-proxy")
        settings = Settings()
        assert settings.root_path == "/dds-proxy"


class TestRootPathOpenApi:
    def test_openapi_available_without_root_path(self):
        application = make_app("")
        client = make_test_client(application)
        resp = client.get("/openapi.json")
        assert resp.status_code == 200
        assert resp.json()["openapi"]
        client.__exit__(None, None, None)

    def test_openapi_available_with_root_path(self):
        application = make_app("/dds-proxy")
        client = make_test_client(application)
        resp = client.get("/openapi.json")
        assert resp.status_code == 200
        assert resp.json()["openapi"]
        client.__exit__(None, None, None)

    def test_openapi_servers_contains_root_path(self):
        application = make_app("/dds-proxy")
        client = make_test_client(application)
        spec = client.get("/openapi.json").json()
        server_urls = [s["url"] for s in spec.get("servers", [])]
        assert "/dds-proxy" in server_urls
        client.__exit__(None, None, None)

    def test_openapi_no_servers_without_root_path(self):
        application = make_app("")
        client = make_test_client(application)
        spec = client.get("/openapi.json").json()
        assert "servers" not in spec or spec["servers"] == [{"url": ""}]
        client.__exit__(None, None, None)


class TestRootPathRoutes:
    def test_routes_still_work_with_root_path(self):
        """root_path must not change route matching — API paths stay the same."""
        application = make_app("/dds-proxy")
        client = make_test_client(application)
        assert client.get("/health").status_code == 200
        assert client.get("/topologies").status_code == 200
        assert client.get("/switching-services").status_code == 200
        assert client.get("/service-termination-points").status_code == 200
        assert client.get("/service-demarcation-points").status_code == 200
        client.__exit__(None, None, None)

    def test_docs_available_with_root_path(self):
        application = make_app("/dds-proxy")
        client = make_test_client(application)
        resp = client.get("/docs")
        assert resp.status_code == 200
        assert "swagger" in resp.text.lower()
        client.__exit__(None, None, None)
