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
"""Tests for /health access log suppression in configure_logging."""

import logging

from dds_proxy.main import configure_logging


def make_access_record(method: str, path: str, status: int = 200) -> logging.LogRecord:
    """Build a LogRecord that mimics uvicorn's real access log format.

    Uvicorn logs access using %-style formatting with a tuple of args, so
    getMessage() assembles the final string at call time. Using the same
    format here ensures the filter is tested against realistic input.
    """
    return logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg='%s - "%s %s HTTP/%s" %d',
        args=("::1:58310", method, path, "1.1", status),
        exc_info=None,
    )


class TestHealthAccessLogSuppression:
    def setup_method(self):
        configure_logging()
        self.access_logger = logging.getLogger("uvicorn.access")
        # There should be exactly one filter attached.
        assert len(self.access_logger.filters) == 1
        self.f = self.access_logger.filters[0]

    def test_health_get_suppressed(self):
        assert self.f.filter(make_access_record("GET", "/health")) is False

    def test_health_post_suppressed(self):
        assert self.f.filter(make_access_record("POST", "/health", 405)) is False

    def test_topologies_not_suppressed(self):
        assert self.f.filter(make_access_record("GET", "/topologies")) is True

    def test_switching_services_not_suppressed(self):
        assert self.f.filter(make_access_record("GET", "/switching-services")) is True

    def test_service_termination_points_not_suppressed(self):
        assert self.f.filter(make_access_record("GET", "/service-termination-points")) is True

    def test_service_demarcation_points_not_suppressed(self):
        assert self.f.filter(make_access_record("GET", "/service-demarcation-points")) is True

    def test_healthz_not_suppressed(self):
        """/healthz must not accidentally match."""
        assert self.f.filter(make_access_record("GET", "/healthz")) is True

    def test_configure_logging_idempotent(self):
        """Calling configure_logging() twice must not stack duplicate filters."""
        configure_logging()
        assert len(self.access_logger.filters) == 1
