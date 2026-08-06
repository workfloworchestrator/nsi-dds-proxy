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

Server verification is always enabled and independent of whether a client certificate is
configured. These tests cover the CA bundle wiring, the optional client certificate, and the
startup validation that rejects certificate paths pointing at missing files.
"""

import ssl
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx2
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from dds_proxy.config import Settings
from dds_proxy.main import app

CERTIFICATE_FIELDS = ["dds_client_cert", "dds_client_key", "dds_ca_bundle"]

# ---------------------------------------------------------------------------
# Helpers
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


def make_settings(**kwargs) -> Settings:
    """Build a Settings instance with all required fields pre-filled.

    Uses ``model_construct`` so lifespan tests bypass field validation and can exercise the SSL
    wiring independently of the path checks covered by TestCertificatePathValidation.
    """
    defaults = {
        "dds_base_url": "https://dds.example.net/dds",
        "dds_client_cert": None,
        "dds_client_key": None,
        "dds_ca_bundle": None,
    }
    defaults.update(kwargs)
    return Settings.model_construct(**defaults)


def capture_verify_argument(settings: Settings) -> object:
    """Run the app lifespan with these settings and return the ``verify`` argument used."""
    captured: list[object] = []
    real_async_client = httpx2.AsyncClient

    def capturing_async_client(**kwargs):
        captured.append(kwargs.get("verify"))
        return real_async_client(**kwargs)

    with (
        patch("dds_proxy.main.settings", settings),
        patch("dds_proxy.main.httpx2.AsyncClient", side_effect=capturing_async_client),
        TestClient(app),
    ):
        pass
    return captured[0]


# ---------------------------------------------------------------------------
# Config-level tests
# ---------------------------------------------------------------------------


class TestCaBundleConfig:
    def test_dds_ca_bundle_defaults_to_none(self):
        settings = make_settings()
        assert settings.dds_ca_bundle is None

    def test_dds_ca_bundle_parsed_as_path(self, cert_files):
        _, _, ca = cert_files
        assert make_settings(dds_ca_bundle=ca).dds_ca_bundle == ca

    def test_dds_ca_bundle_loaded_from_env(self, monkeypatch, cert_files):
        _, _, ca = cert_files
        monkeypatch.setenv("DDS_CA_BUNDLE", str(ca))
        assert Settings().dds_ca_bundle == Path(ca)


class TestCertificatePathValidation:
    """A configured certificate path must exist, so misconfiguration fails at startup."""

    @pytest.mark.parametrize("field", CERTIFICATE_FIELDS)
    def test_missing_file_is_rejected(self, tmp_path, field):
        with pytest.raises(ValidationError, match="file not found"):
            Settings(**{field: tmp_path / "does-not-exist.pem"})

    @pytest.mark.parametrize("field", CERTIFICATE_FIELDS)
    def test_existing_file_is_accepted(self, cert_files, field):
        cert, _, _ = cert_files
        assert getattr(Settings(**{field: cert}), field) == cert

    @pytest.mark.parametrize("field", CERTIFICATE_FIELDS)
    def test_unset_is_accepted(self, field):
        assert getattr(Settings(**{field: None}), field) is None


# ---------------------------------------------------------------------------
# Lifespan SSL context tests
# ---------------------------------------------------------------------------


class TestServerVerificationAlwaysEnabled:
    def test_verifying_context_used_without_client_certificate(self):
        """The DDS server is verified even when no client certificate is configured.

        Regression test: this path previously passed ``verify=False``, disabling server
        certificate and hostname checks entirely.
        """
        verify = capture_verify_argument(make_settings())

        assert isinstance(verify, ssl.SSLContext)
        assert verify.verify_mode == ssl.CERT_REQUIRED
        assert verify.check_hostname is True


class TestCaBundleWiring:
    @pytest.mark.parametrize(
        "with_ca_bundle",
        [
            pytest.param(True, id="ca-bundle-set"),
            pytest.param(False, id="ca-bundle-unset-uses-system-trust-store"),
        ],
    )
    def test_ca_bundle_passed_to_create_default_context(self, cert_files, with_ca_bundle):
        _, _, ca = cert_files
        expected = ca if with_ca_bundle else None
        settings = make_settings(dds_ca_bundle=expected)

        with (
            patch("dds_proxy.main.settings", settings),
            patch("dds_proxy.main.ssl.create_default_context", return_value=MagicMock(spec=ssl.SSLContext)) as create,
            TestClient(app),
        ):
            create.assert_called_once_with(cafile=expected)


class TestClientCertificateWiring:
    def test_client_cert_loaded_when_cert_and_key_are_set(self, cert_files):
        cert, key, ca = cert_files
        settings = make_settings(dds_client_cert=cert, dds_client_key=key, dds_ca_bundle=ca)
        mock_ctx = MagicMock(spec=ssl.SSLContext)

        with (
            patch("dds_proxy.main.settings", settings),
            patch("dds_proxy.main.ssl.create_default_context", return_value=mock_ctx),
            TestClient(app),
        ):
            mock_ctx.load_cert_chain.assert_called_once_with(certfile=cert, keyfile=key)

    @pytest.mark.parametrize(
        ("with_cert", "with_key"),
        [
            pytest.param(False, False, id="neither-set"),
            pytest.param(True, False, id="cert-without-key"),
            pytest.param(False, True, id="key-without-cert"),
        ],
    )
    def test_client_cert_not_loaded_when_incomplete(self, cert_files, with_cert, with_key):
        cert, key, _ = cert_files
        settings = make_settings(
            dds_client_cert=cert if with_cert else None,
            dds_client_key=key if with_key else None,
        )
        mock_ctx = MagicMock(spec=ssl.SSLContext)

        with (
            patch("dds_proxy.main.settings", settings),
            patch("dds_proxy.main.ssl.create_default_context", return_value=mock_ctx),
            TestClient(app),
        ):
            mock_ctx.load_cert_chain.assert_not_called()
