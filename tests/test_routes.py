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
"""Route integration tests for dds-proxy.

Uses FastAPI's TestClient with a mocked HTTP client injected onto app.state,
so no real network calls are made.
"""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from dds_proxy import dds_client
from dds_proxy.main import app
from tests.conftest import (
    PORT_A,
    PORT_Z,
    SIMPLE_COLLECTION,
    SS_ID,
    TOPO_ID,
    make_dds_collection,
    make_nml_topology,
)

# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def clear_cache():
    """Ensure every test starts with an empty DDS cache."""
    with patch.dict(dds_client._cache, {}, clear=True):
        yield


@pytest.fixture
def api():
    """TestClient with a mock HTTP client injected onto app.state.

    We enter the TestClient context first (which runs the lifespan and creates
    a real httpx.AsyncClient), then immediately overwrite app.state.http_client
    with our mock so all requests during the test use it instead.
    """
    mock_http = AsyncMock()
    mock_response = AsyncMock()
    mock_response.content = SIMPLE_COLLECTION
    mock_response.raise_for_status = lambda: None
    mock_http.get = AsyncMock(return_value=mock_response)

    with TestClient(app, raise_server_exceptions=True) as client:
        # Overwrite after lifespan has run
        app.state.http_client = mock_http
        yield client, mock_http


# ---------------------------------------------------------------------------
# /health
# ---------------------------------------------------------------------------


class TestHealth:
    def test_health_returns_200(self, api):
        client, _ = api
        resp = client.get("/health")
        assert resp.status_code == 200

    def test_health_body(self, api):
        client, _ = api
        assert client.get("/health").json() == {"status": "ok"}


# ---------------------------------------------------------------------------
# GET /topologies
# ---------------------------------------------------------------------------


class TestTopologiesRoute:
    def test_returns_200(self, api):
        client, _ = api
        assert client.get("/topologies").status_code == 200

    def test_returns_list(self, api):
        client, _ = api
        data = client.get("/topologies").json()
        assert isinstance(data, list)
        assert len(data) == 2

    def test_topology_has_required_fields(self, api):
        client, _ = api
        topo = client.get("/topologies").json()[0]
        assert "id" in topo
        assert "version" in topo
        assert "name" in topo
        assert "lifetime" in topo
        assert "start" in topo["lifetime"]
        assert "end" in topo["lifetime"]

    def test_topology_id_correct(self, api):
        client, _ = api
        topo = client.get("/topologies").json()[0]
        assert topo["id"] == TOPO_ID

    def test_502_on_dds_failure(self, api):
        client, mock_http = api
        mock_http.get.side_effect = Exception("connection refused")
        resp = client.get("/topologies")
        assert resp.status_code == 502

    def test_502_body_contains_detail(self, api):
        client, mock_http = api
        mock_http.get.side_effect = Exception("timeout")
        resp = client.get("/topologies")
        assert "DDS fetch failed" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# GET /switching-services
# ---------------------------------------------------------------------------


class TestSwitchingServicesRoute:
    def test_returns_200(self, api):
        client, _ = api
        assert client.get("/switching-services").status_code == 200

    def test_returns_list(self, api):
        client, _ = api
        data = client.get("/switching-services").json()
        assert isinstance(data, list)
        assert len(data) == 2

    def test_switching_service_fields(self, api):
        client, _ = api
        ss = client.get("/switching-services").json()[0]
        assert ss["id"] == SS_ID
        assert ss["labelSwapping"] is True
        assert "encoding" in ss
        assert "labelType" in ss
        assert "topologyId" in ss

    def test_topology_id_matches(self, api):
        client, _ = api
        ss = client.get("/switching-services").json()[0]
        assert ss["topologyId"] == TOPO_ID

    def test_502_on_dds_failure(self, api):
        client, mock_http = api
        mock_http.get.side_effect = Exception("upstream error")
        assert client.get("/switching-services").status_code == 502


# ---------------------------------------------------------------------------
# GET /service-termination-points
# ---------------------------------------------------------------------------


class TestSTPsRoute:
    def test_returns_200(self, api):
        client, _ = api
        assert client.get("/service-termination-points").status_code == 200

    def test_returns_list(self, api):
        client, _ = api
        data = client.get("/service-termination-points").json()
        assert isinstance(data, list)
        assert len(data) == 2

    def test_stp_fields(self, api):
        client, _ = api
        stps = client.get("/service-termination-points").json()
        stp = next(s for s in stps if s["id"] == PORT_A)
        assert stp["name"] == "Port One"
        assert stp["capacity"] == 100000
        assert stp["labelGroup"] == "100-200"
        assert stp["switchingServiceId"] == SS_ID

    def test_502_on_dds_failure(self, api):
        client, mock_http = api
        mock_http.get.side_effect = Exception("upstream error")
        assert client.get("/service-termination-points").status_code == 502


# ---------------------------------------------------------------------------
# GET /service-demarcation-points
# ---------------------------------------------------------------------------


class TestSDPsRoute:
    def test_returns_200(self, api):
        client, _ = api
        assert client.get("/service-demarcation-points").status_code == 200

    def test_returns_list(self, api):
        client, _ = api
        data = client.get("/service-demarcation-points").json()
        assert isinstance(data, list)
        assert len(data) == 1

    def test_sdp_fields(self, api):
        client, _ = api
        sdps = client.get("/service-demarcation-points").json()
        stp_pairs = {(s["stpAId"], s["stpZId"]) for s in sdps}
        assert (PORT_A, PORT_Z) in stp_pairs or (PORT_Z, PORT_A) in stp_pairs

    def test_empty_when_no_aliases(self, api):
        client, mock_http = api
        nml = make_nml_topology(ports=[{"id": PORT_A, "name": "P", "capacity": 0, "label_group": ""}])
        collection = make_dds_collection([{"id": TOPO_ID, "nml_bytes": nml}])
        # Update the mock to return the new collection, then clear cache
        mock_response = AsyncMock()
        mock_response.content = collection
        mock_response.raise_for_status = lambda: None
        mock_http.get = AsyncMock(return_value=mock_response)
        dds_client._cache.clear()

        data = client.get("/service-demarcation-points").json()
        assert data == []

    def test_502_on_dds_failure(self, api):
        client, mock_http = api
        mock_http.get.side_effect = Exception("timeout")
        assert client.get("/service-demarcation-points").status_code == 502
