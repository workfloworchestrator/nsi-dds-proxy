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
"""Tests for the authentication dependency.

Authentication is performed at the edge proxy (Traefik + oauth2-proxy for the
portal route, nsi-auth for the mTLS route) and the resulting identity is
forwarded as request headers. The dependency reads ``X-Auth-Request-Email``
plus ``X-Auth-Request-Groups`` on the OIDC path, and the configured
``mtls_header`` (with ``X-Client-DN``) on the mTLS path.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from dds_proxy.auth import check_groups
from dds_proxy.main import app
from tests.conftest import SIMPLE_COLLECTION


def _mock_dds_http_client() -> AsyncMock:
    """Mock HTTP client returning the canned DDS collection."""
    mock_http = AsyncMock()
    mock_response = AsyncMock()
    mock_response.content = SIMPLE_COLLECTION
    mock_response.raise_for_status = lambda: None
    mock_http.get = AsyncMock(return_value=mock_response)
    return mock_http


@contextmanager
def _auth_client(**setting_overrides: Any) -> Iterator[TestClient]:
    """TestClient with auth.settings patched and a stub DDS http client installed."""
    base: dict[str, Any] = {
        "auth_enabled": True,
        "mtls_header": "",
        "oidc_required_groups": [],
    }
    base.update(setting_overrides)
    with (
        patch.multiple("dds_proxy.auth.settings", **base),
        TestClient(app, raise_server_exceptions=False) as client,
    ):
        app.state.http_client = _mock_dds_http_client()
        yield client


@pytest.mark.parametrize(
    ("headers", "required_groups", "mtls_header", "expected_status"),
    [
        pytest.param(
            {"X-Auth-Request-Email": "alice@example.org"},
            [],
            "",
            200,
            id="oidc-email-no-groups-required",
        ),
        pytest.param(
            {"X-Auth-Request-Email": "alice@example.org", "X-Auth-Request-Groups": "developer,viewer"},
            ["developer"],
            "",
            200,
            id="oidc-group-match-comma-separated",
        ),
        pytest.param(
            {"X-Auth-Request-Email": "alice@example.org", "X-Auth-Request-Groups": "developer viewer"},
            ["viewer"],
            "",
            200,
            id="oidc-group-match-space-separated",
        ),
        pytest.param(
            {"X-Auth-Request-Email": "alice@example.org", "X-Auth-Request-Groups": "viewer"},
            ["developer"],
            "",
            403,
            id="oidc-group-mismatch",
        ),
        pytest.param(
            {"X-Auth-Request-Email": "alice@example.org"},
            ["developer"],
            "",
            403,
            id="oidc-groups-header-missing-when-required",
        ),
        pytest.param(
            {},
            [],
            "",
            401,
            id="no-identity-no-mtls",
        ),
        pytest.param(
            {"X-Auth-Method": "mTLS", "X-Client-DN": "CN=Test"},
            [],
            "X-Auth-Method",
            200,
            id="mtls-happy-path",
        ),
        pytest.param(
            {"X-Auth-Method": "mTLS"},
            [],
            "X-Auth-Method",
            200,
            id="mtls-without-client-dn",
        ),
        pytest.param(
            {"X-Auth-Method": "   "},
            [],
            "X-Auth-Method",
            401,
            id="mtls-header-whitespace",
        ),
        pytest.param(
            {"X-Auth-Method": ""},
            [],
            "X-Auth-Method",
            401,
            id="mtls-header-empty",
        ),
        pytest.param(
            {
                "X-Auth-Request-Email": "alice@example.org",
                "X-Auth-Request-Groups": "developer",
                "X-Auth-Method": "mTLS",
            },
            ["developer"],
            "X-Auth-Method",
            200,
            id="oidc-wins-when-both-present",
        ),
        pytest.param(
            {"X-Auth-Request-Email": "   "},
            [],
            "",
            401,
            id="oidc-whitespace-email-treated-as-absent",
        ),
        pytest.param(
            {"X-Auth-Request-Email": "   "},
            [],
            "X-Auth-Method",
            401,
            id="oidc-whitespace-email-does-not-fall-through-to-mtls",
        ),
        pytest.param(
            {},
            [],
            "X-Auth-Method",
            401,
            id="mtls-configured-but-header-missing",
        ),
        pytest.param(
            {"X-Auth-Request-Email": "alice@example.org", "X-Client-DN": "CN=spoofed"},
            [],
            "X-Auth-Method",
            200,
            id="x-client-dn-cannot-divert-to-mtls-branch",
        ),
        pytest.param(
            {"X-Auth-Request-Email": "alice@example.org", "X-Auth-Request-Groups": "developer , viewer "},
            ["viewer"],
            "",
            200,
            id="oidc-groups-tolerate-whitespace-around-commas",
        ),
    ],
)
def test_get_authenticated_user(
    headers: dict[str, str],
    required_groups: list[str],
    mtls_header: str,
    expected_status: int,
) -> None:
    with _auth_client(oidc_required_groups=required_groups, mtls_header=mtls_header) as client:
        resp = client.get("/topologies", headers=headers)
        assert resp.status_code == expected_status


def test_401_response_advertises_bearer_scheme() -> None:
    with _auth_client() as client:
        resp = client.get("/topologies")
        assert resp.status_code == 401
        assert resp.headers.get("www-authenticate") == "Bearer"


@pytest.mark.parametrize(
    ("auth_enabled", "path", "expected_status"),
    [
        pytest.param(True, "/openapi.json", 404, id="openapi-hidden-with-auth"),
        pytest.param(True, "/docs", 404, id="docs-hidden-with-auth"),
        pytest.param(True, "/redoc", 404, id="redoc-hidden-with-auth"),
        pytest.param(False, "/openapi.json", 200, id="openapi-served-without-auth"),
        pytest.param(False, "/docs", 200, id="docs-served-without-auth"),
        pytest.param(False, "/redoc", 200, id="redoc-served-without-auth"),
    ],
)
def test_openapi_and_docs_exposure(auth_enabled: bool, path: str, expected_status: int) -> None:
    from dds_proxy.main import create_app

    with patch.multiple("dds_proxy.main.settings", auth_enabled=auth_enabled):
        scoped_app = create_app()
    with TestClient(scoped_app, raise_server_exceptions=False) as client:
        scoped_app.state.http_client = _mock_dds_http_client()
        assert client.get(path).status_code == expected_status


def test_auth_disabled_lets_everything_through() -> None:
    with (
        patch.multiple("dds_proxy.auth.settings", auth_enabled=False),
        TestClient(app, raise_server_exceptions=True) as client,
    ):
        app.state.http_client = _mock_dds_http_client()
        resp = client.get("/topologies")
        assert resp.status_code == 200


def test_health_is_always_unprotected() -> None:
    with _auth_client() as client:
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


@pytest.mark.parametrize(
    ("user_groups", "required", "expected"),
    [
        pytest.param(["admin", "user"], ["admin"], ["admin"], id="single-required-match"),
        pytest.param(["a", "b", "c"], ["c", "d"], ["c"], id="partial-intersection"),
        pytest.param(["c", "a", "b"], ["a", "b"], ["a", "b"], id="result-is-sorted"),
        pytest.param(["viewer"], ["admin"], [], id="no-match-returns-empty"),
        pytest.param([], ["admin"], [], id="empty-user-groups-returns-empty"),
        pytest.param(["admin"], [], [], id="no-required-groups-returns-empty"),
    ],
)
def test_check_groups(user_groups: list[str], required: list[str], expected: list[str]) -> None:
    assert check_groups(user_groups, required) == expected


@pytest.mark.parametrize(
    ("env_value", "expected"),
    [
        pytest.param('["g1","g2"]', ["g1", "g2"], id="json-array"),
        pytest.param("g1,g2,g3", ["g1", "g2", "g3"], id="comma-separated"),
        pytest.param("single-group", ["single-group"], id="single-value"),
        pytest.param("", [], id="empty-string"),
    ],
)
def test_required_groups_parsing(env_value: str, expected: list[str]) -> None:
    from dds_proxy.config import Settings

    assert Settings(oidc_required_groups=env_value).oidc_required_groups == expected


def test_malformed_json_groups_raises_validation_error() -> None:
    from pydantic import ValidationError

    from dds_proxy.config import Settings

    with pytest.raises(ValidationError, match="Invalid JSON"):
        Settings(oidc_required_groups='["g1", invalid')
