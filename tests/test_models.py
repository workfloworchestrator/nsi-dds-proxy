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
"""
Unit tests for app/models.py — verifies field aliases and serialisation.
"""

import pytest
from dds_proxy.models import (
    Lifetime,
    Topology,
    SwitchingService,
    ServiceTerminationPoint,
    ServiceDemarcationPoint,
)


class TestLifetime:

    def test_basic(self):
        lt = Lifetime(start="2025-01-01T00:00:00Z", end="2026-01-01T00:00:00Z")
        assert lt.start == "2025-01-01T00:00:00Z"
        assert lt.end == "2026-01-01T00:00:00Z"


class TestTopology:

    def test_construct(self):
        t = Topology(
            id="urn:ogf:network:example.net:2020:topology",
            version="2026-01-01T00:00:00Z",
            name="Example",
            lifetime=Lifetime(start="2025-01-01T00:00:00Z", end="2026-01-01T00:00:00Z"),
        )
        assert t.id == "urn:ogf:network:example.net:2020:topology"
        assert t.name == "Example"


class TestSwitchingService:

    def test_construct_with_python_names(self):
        ss = SwitchingService(
            id="urn:ogf:network:example.net:2020:topology:switch",
            encoding="http://schemas.ogf.org/nml/2012/10/ethernet",
            label_swapping=True,
            label_type="http://schemas.ogf.org/nml/2012/10/ethernet#vlan",
            topology_id="urn:ogf:network:example.net:2020:topology",
        )
        assert ss.label_swapping is True

    def test_serialises_with_camel_aliases(self):
        ss = SwitchingService(
            id="x",
            encoding="enc",
            label_swapping=False,
            label_type="lt",
            topology_id="tid",
        )
        data = ss.model_dump(by_alias=True)
        assert "labelSwapping" in data
        assert "labelType" in data
        assert "topologyId" in data

    def test_label_swapping_false(self):
        ss = SwitchingService(
            id="x", encoding="", label_swapping=False, label_type="", topology_id=""
        )
        assert ss.label_swapping is False


class TestServiceTerminationPoint:

    def test_construct(self):
        stp = ServiceTerminationPoint(
            id="urn:ogf:network:example.net:2020:topology:port-1",
            name="Port 1",
            capacity=100000,
            label_group="100-200",
            switching_service_id="urn:ogf:network:example.net:2020:topology:switch",
        )
        assert stp.capacity == 100000
        assert stp.label_group == "100-200"

    def test_serialises_with_pascal_aliases(self):
        stp = ServiceTerminationPoint(
            id="x", name="n", capacity=0, label_group="lg", switching_service_id="ssid"
        )
        data = stp.model_dump(by_alias=True)
        assert "LabelGroup" in data
        assert "SwitchingServiceId" in data


class TestServiceDemarcationPoint:

    def test_construct(self):
        sdp = ServiceDemarcationPoint(
            stp_a_id="urn:ogf:network:example.net:2020:topology:port-1",
            stp_z_id="urn:ogf:network:other.net:2021:topology:port-99",
        )
        assert sdp.stp_a_id == "urn:ogf:network:example.net:2020:topology:port-1"

    def test_serialises_with_pascal_aliases(self):
        sdp = ServiceDemarcationPoint(stp_a_id="a", stp_z_id="z")
        data = sdp.model_dump(by_alias=True)
        assert data["StpAId"] == "a"
        assert data["StpZId"] == "z"
