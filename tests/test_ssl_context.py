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
"""Tests for SSL context construction in the lifespan.

Verifies that the CA bundle, client cert/key, and fallback behavior are
wired up correctly depending on which settings are present.
"""

import ssl
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

from dds_proxy.config import Settings
from dds_proxy.main import app

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_settings(**kwargs) -> Settings:
    """Build a Settings instance with all required fields pre-filled.

    Any keyword argument overrides the corresponding field. File-path fields
    default to None so callers only need to supply what they care about.
    """
    defaults = {
        "dds_base_url": "https://dds.example.net/dds",
        "dds_client_cert": None,
        "dds_client_key": None,
        "dds_ca_bundle": None,
    }
    defaults.update(kwargs)
    return Settings.model_construct(**defaults)


# ---------------------------------------------------------------------------
# Config-level tests
# ---------------------------------------------------------------------------


class TestCaBundleConfig:
    def test_dds_ca_bundle_defaults_to_none(self):
        settings = make_settings()
        assert settings.dds_ca_bundle is None

    def test_dds_ca_bundle_parsed_as_path(self):
        settings = make_settings(dds_ca_bundle=Path("/etc/ssl/ca-bundle.pem"))
        assert settings.dds_ca_bundle == Path("/etc/ssl/ca-bundle.pem")

    def test_dds_ca_bundle_loaded_from_env(self, monkeypatch):
        monkeypatch.setenv("DDS_CA_BUNDLE", "/etc/ssl/ca-bundle.pem")
        settings = Settings()
        assert settings.dds_ca_bundle == Path("/etc/ssl/ca-bundle.pem")


# ---------------------------------------------------------------------------
# Lifespan SSL context tests
# ---------------------------------------------------------------------------


@pytest.fixture
def cert_files(tmp_path):
    """Create temporary cert, key, and CA bundle files."""
    cert = tmp_path / "client.pem"
    key = tmp_path / "client.key"
    ca = tmp_path / "ca-bundle.pem"
    cert.write_text("cert")
    key.write_text("key")
    ca.write_text("ca")
    return cert, key, ca


class TestSslContextWithCaBundle:
    def test_load_verify_locations_called_with_ca_bundle(self, cert_files):
        """Test that load_verify_locations is called with CA bundle.

        When DDS_CA_BUNDLE is set and exists, load_verify_locations is called
        with the bundle path and load_default_certs is not called.
        """
        cert, key, ca = cert_files
        settings = make_settings(dds_client_cert=cert, dds_client_key=key, dds_ca_bundle=ca)

        mock_ctx = MagicMock(spec=ssl.SSLContext)

        with (
            patch("dds_proxy.main.settings", settings),
            patch("dds_proxy.main.ssl.SSLContext", return_value=mock_ctx),
            TestClient(app),
        ):
            mock_ctx.load_verify_locations.assert_called_once_with(cafile=ca)
            mock_ctx.load_default_certs.assert_not_called()

    def test_load_cert_chain_called_with_client_cert_and_key(self, cert_files):
        """The client cert and key are always loaded when all three paths are present."""
        cert, key, ca = cert_files
        settings = make_settings(dds_client_cert=cert, dds_client_key=key, dds_ca_bundle=ca)

        mock_ctx = MagicMock(spec=ssl.SSLContext)

        with (
            patch("dds_proxy.main.settings", settings),
            patch("dds_proxy.main.ssl.SSLContext", return_value=mock_ctx),
            TestClient(app),
        ):
            mock_ctx.load_cert_chain.assert_called_once_with(certfile=cert, keyfile=key)

    def test_ssl_context_uses_protocol_tls_client(self, cert_files):
        """SSLContext is constructed with PROTOCOL_TLS_CLIENT, not via create_default_context."""
        cert, key, ca = cert_files
        settings = make_settings(dds_client_cert=cert, dds_client_key=key, dds_ca_bundle=ca)

        mock_ctx = MagicMock(spec=ssl.SSLContext)
        calls = []

        def fake_ssl_context(protocol):
            calls.append(protocol)
            return mock_ctx

        with (
            patch("dds_proxy.main.settings", settings),
            patch("dds_proxy.main.ssl.SSLContext", side_effect=fake_ssl_context),
            TestClient(app),
        ):
            assert calls == [ssl.PROTOCOL_TLS_CLIENT]


class TestSslContextWithoutCaBundle:
    def test_load_default_certs_called_when_ca_bundle_not_set(self, cert_files):
        """When DDS_CA_BUNDLE is unset, load_default_certs is called as a fallback."""
        cert, key, _ = cert_files
        settings = make_settings(dds_client_cert=cert, dds_client_key=key, dds_ca_bundle=None)

        mock_ctx = MagicMock(spec=ssl.SSLContext)

        with (
            patch("dds_proxy.main.settings", settings),
            patch("dds_proxy.main.ssl.SSLContext", return_value=mock_ctx),
            TestClient(app),
        ):
            mock_ctx.load_default_certs.assert_called_once()
            mock_ctx.load_verify_locations.assert_not_called()

    def test_load_default_certs_called_when_ca_bundle_file_missing(self, cert_files, tmp_path):
        """When DDS_CA_BUNDLE points to a non-existent file, fall back to load_default_certs."""
        cert, key, _ = cert_files
        missing = tmp_path / "does-not-exist.pem"
        settings = make_settings(dds_client_cert=cert, dds_client_key=key, dds_ca_bundle=missing)

        mock_ctx = MagicMock(spec=ssl.SSLContext)

        with (
            patch("dds_proxy.main.settings", settings),
            patch("dds_proxy.main.ssl.SSLContext", return_value=mock_ctx),
            TestClient(app),
        ):
            mock_ctx.load_default_certs.assert_called_once()
            mock_ctx.load_verify_locations.assert_not_called()


class TestSslContextSkippedWithoutClientCert:
    def test_verify_false_when_no_cert_configured(self, cert_files):
        """httpx.AsyncClient is created with verify=False when no client cert is set."""
        cert, key, _ = cert_files
        settings = make_settings(dds_client_cert=None, dds_client_key=None, dds_ca_bundle=None)
        verify_values = []

        real_async_client = httpx.AsyncClient

        def capturing_async_client(**kwargs):
            verify_values.append(kwargs.get("verify"))
            return real_async_client(**kwargs)

        with (
            patch("dds_proxy.main.settings", settings),
            patch("dds_proxy.main.httpx.AsyncClient", side_effect=capturing_async_client),
            TestClient(app),
        ):
            assert verify_values == [False]
