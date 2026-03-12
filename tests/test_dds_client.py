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
"""Unit tests for app/dds_client.py.

These tests exercise the XML parsing logic directly, bypassing HTTP by
injecting pre-built NML documents into _get_topology_documents.
"""

import base64
from unittest.mock import AsyncMock, patch

import pytest

from dds_proxy import dds_client
from dds_proxy.config import settings
from tests.conftest import (
    DDS_NS,
    ETH_NS,
    NML_NS,
    PORT_A,
    PORT_Z,
    PORT_Z_IN,
    SIMPLE_COLLECTION,
    SS_ID,
    TOPO_ID,
    TOPO_ID_2,
    make_dds_collection,
    make_nml_topology,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_mock_http(content: bytes) -> AsyncMock:
    mock_http = AsyncMock()
    mock_response = AsyncMock()
    mock_response.content = content
    mock_response.raise_for_status = lambda: None
    mock_http.get = AsyncMock(return_value=mock_response)
    return mock_http


# ---------------------------------------------------------------------------
# _get_topology_documents
# ---------------------------------------------------------------------------


class TestGetTopologyDocuments:
    @pytest.mark.asyncio
    async def test_document_without_type_element_is_skipped(self):
        """A DDS document that has no <type> child element is silently skipped."""
        no_type_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<ns0:collection xmlns:ns0="{DDS_NS}">
  <ns0:documents>
    <ns0:document id="{TOPO_ID}" version="2026-01-01T00:00:00Z" xmlns:ns0="{DDS_NS}">
    </ns0:document>
  </ns0:documents>
</ns0:collection>""".encode()
        client = make_mock_http(no_type_xml)
        with patch.dict(dds_client._cache, {}, clear=True):
            result = await dds_client.fetch_topologies(client, "https://dds.example.net/dds")
        assert result == []

    @pytest.mark.asyncio
    async def test_corrupt_inline_content_is_skipped(self):
        """A document whose base64/gzip content is corrupt is silently skipped."""
        corrupt_content = base64.b64encode(b"this is not gzip data").decode()
        corrupt_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<ns0:collection xmlns:ns0="{DDS_NS}">
  <ns0:documents>
    <ns0:document id="{TOPO_ID}" version="2026-01-01T00:00:00Z" xmlns:ns0="{DDS_NS}">
      <type>vnd.ogf.nsi.topology.v2+xml</type>
      <content contentType="application/x-gzip" contentTransferEncoding="base64">{corrupt_content}</content>
    </ns0:document>
  </ns0:documents>
</ns0:collection>""".encode()
        client = make_mock_http(corrupt_xml)
        with patch.dict(dds_client._cache, {}, clear=True):
            result = await dds_client.fetch_topologies(client, "https://dds.example.net/dds")
        assert result == []

    @pytest.mark.asyncio
    async def test_href_fetch_success(self):
        """A document without inline content is fetched from its href attribute."""
        nml = make_nml_topology(topo_id=TOPO_ID, name="Href Topology")
        href_url = "https://dds.example.net/dds/nml/topo"
        collection_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<ns0:collection xmlns:ns0="{DDS_NS}">
  <ns0:documents>
    <ns0:document
        id="{TOPO_ID}"
        href="{href_url}"
        version="2026-01-01T00:00:00Z"
        expires="2026-12-31T00:00:00Z"
        xmlns:ns0="{DDS_NS}">
      <type>vnd.ogf.nsi.topology.v2+xml</type>
    </ns0:document>
  </ns0:documents>
</ns0:collection>""".encode()

        collection_resp = AsyncMock()
        collection_resp.content = collection_xml
        collection_resp.raise_for_status = lambda: None

        href_resp = AsyncMock()
        href_resp.content = nml
        href_resp.raise_for_status = lambda: None

        mock_http = AsyncMock()
        mock_http.get = AsyncMock(side_effect=[collection_resp, href_resp])

        with patch.dict(dds_client._cache, {}, clear=True):
            result = await dds_client.fetch_topologies(mock_http, "https://dds.example.net/dds")
        assert len(result) == 1
        assert result[0].id == TOPO_ID
        assert mock_http.get.call_count == 2

    @pytest.mark.asyncio
    async def test_href_fetch_failure_is_skipped(self):
        """A document whose href fetch raises an exception is silently skipped."""
        href_url = "https://dds.example.net/dds/nml/topo"
        collection_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<ns0:collection xmlns:ns0="{DDS_NS}">
  <ns0:documents>
    <ns0:document
        id="{TOPO_ID}"
        href="{href_url}"
        version="2026-01-01T00:00:00Z"
        expires="2026-12-31T00:00:00Z"
        xmlns:ns0="{DDS_NS}">
      <type>vnd.ogf.nsi.topology.v2+xml</type>
    </ns0:document>
  </ns0:documents>
</ns0:collection>""".encode()

        collection_resp = AsyncMock()
        collection_resp.content = collection_xml
        collection_resp.raise_for_status = lambda: None

        mock_http = AsyncMock()
        mock_http.get = AsyncMock(side_effect=[collection_resp, Exception("connection refused")])

        with patch.dict(dds_client._cache, {}, clear=True):
            result = await dds_client.fetch_topologies(mock_http, "https://dds.example.net/dds")
        assert result == []


# ---------------------------------------------------------------------------
# fetch_topologies
# ---------------------------------------------------------------------------


class TestFetchTopologies:
    @pytest.mark.asyncio
    async def test_returns_topology_with_correct_id(self):
        client = make_mock_http(SIMPLE_COLLECTION)
        with patch.dict(dds_client._cache, {}, clear=True):
            result = await dds_client.fetch_topologies(client, "https://dds.example.net/dds")
        assert len(result) == 2
        assert any(t.id == TOPO_ID for t in result)

    @pytest.mark.asyncio
    async def test_returns_topology_name(self):
        client = make_mock_http(SIMPLE_COLLECTION)
        with patch.dict(dds_client._cache, {}, clear=True):
            result = await dds_client.fetch_topologies(client, "https://dds.example.net/dds")
        assert result[0].name == "Example Network"

    @pytest.mark.asyncio
    async def test_lifetime_populated_from_nml(self):
        client = make_mock_http(SIMPLE_COLLECTION)
        with patch.dict(dds_client._cache, {}, clear=True):
            result = await dds_client.fetch_topologies(client, "https://dds.example.net/dds")
        assert result[0].lifetime.start == "2025-01-01T00:00:00Z"
        assert result[0].lifetime.end == "2026-01-01T00:00:00Z"

    @pytest.mark.asyncio
    async def test_version_from_dds_document(self):
        client = make_mock_http(SIMPLE_COLLECTION)
        with patch.dict(dds_client._cache, {}, clear=True):
            result = await dds_client.fetch_topologies(client, "https://dds.example.net/dds")
        assert result[0].version == "2026-01-01T00:00:00Z"

    @pytest.mark.asyncio
    async def test_multiple_topologies(self):
        client = make_mock_http(SIMPLE_COLLECTION)
        with patch.dict(dds_client._cache, {}, clear=True):
            result = await dds_client.fetch_topologies(client, "https://dds.example.net/dds")
        assert len(result) == 2
        ids = {t.id for t in result}
        assert TOPO_ID in ids
        assert TOPO_ID_2 in ids

    @pytest.mark.asyncio
    async def test_empty_collection_returns_empty_list(self):
        collection = make_dds_collection([])
        client = make_mock_http(collection)
        with patch.dict(dds_client._cache, {}, clear=True):
            result = await dds_client.fetch_topologies(client, "https://dds.example.net/dds")
        assert result == []

    @pytest.mark.asyncio
    async def test_lifetime_fallback_from_dds_attributes(self):
        """When the NML document has no <nml:Lifetime>, version and expires from the DDS document are used."""
        nml_no_lifetime = f"""<?xml version="1.0" encoding="UTF-8"?>
<nml:Topology
    id="{TOPO_ID}"
    name="No Lifetime Topology"
    xmlns:nml="{NML_NS}"
    xmlns:eth="{ETH_NS}">
</nml:Topology>""".encode("utf-8")
        collection = make_dds_collection(
            [
                {
                    "id": TOPO_ID,
                    "nml_bytes": nml_no_lifetime,
                    "version": "2025-06-01T00:00:00Z",
                    "expires": "2026-06-01T00:00:00Z",
                }
            ]
        )
        client = make_mock_http(collection)
        with patch.dict(dds_client._cache, {}, clear=True):
            result = await dds_client.fetch_topologies(client, "https://dds.example.net/dds")
        assert len(result) == 1
        assert result[0].lifetime.start == "2025-06-01T00:00:00Z"
        assert result[0].lifetime.end == "2026-06-01T00:00:00Z"

    @pytest.mark.asyncio
    async def test_nsa_documents_are_skipped(self):
        """Documents with type vnd.ogf.nsi.nsa.v1+xml must not be parsed as topologies."""
        nsa_collection = """<?xml version="1.0" encoding="UTF-8"?>
<ns0:collection xmlns:ns0="http://schemas.ogf.org/nsi/2014/02/discovery/types">
  <ns0:documents>
    <ns0:document id="urn:ogf:network:example.net:2020:nsa"
        href="https://dds.example.net/dds/documents/nsa"
        version="2026-01-01T00:00:00Z" expires="2026-12-31T00:00:00Z">
      <ns0:type>vnd.ogf.nsi.nsa.v1+xml</ns0:type>
      <ns0:content contentType="application/x-gzip" contentTransferEncoding="base64">dummydata</ns0:content>
    </ns0:document>
  </ns0:documents>
</ns0:collection>""".encode()
        client = make_mock_http(nsa_collection)
        with patch.dict(dds_client._cache, {}, clear=True):
            result = await dds_client.fetch_topologies(client, "https://dds.example.net/dds")
        assert result == []


# ---------------------------------------------------------------------------
# fetch_switching_services
# ---------------------------------------------------------------------------


class TestFetchSwitchingServices:
    @pytest.mark.asyncio
    async def test_returns_switching_service(self):
        client = make_mock_http(SIMPLE_COLLECTION)
        with patch.dict(dds_client._cache, {}, clear=True):
            result = await dds_client.fetch_switching_services(client, "https://dds.example.net/dds")
        assert len(result) == 2
        assert any(s.id == SS_ID for s in result)

    @pytest.mark.asyncio
    async def test_label_swapping_parsed_as_bool(self):
        client = make_mock_http(SIMPLE_COLLECTION)
        with patch.dict(dds_client._cache, {}, clear=True):
            result = await dds_client.fetch_switching_services(client, "https://dds.example.net/dds")
        assert result[0].label_swapping is True

    @pytest.mark.asyncio
    async def test_label_swapping_false(self):
        nml = make_nml_topology(
            switching_services=[
                {
                    "id": SS_ID,
                    "encoding": "http://schemas.ogf.org/nml/2012/10/ethernet",
                    "labelSwapping": False,
                    "labelType": "http://schemas.ogf.org/nml/2012/10/ethernet#vlan",
                    "port_refs": [],
                }
            ]
        )
        collection = make_dds_collection([{"id": TOPO_ID, "nml_bytes": nml}])
        client = make_mock_http(collection)
        with patch.dict(dds_client._cache, {}, clear=True):
            result = await dds_client.fetch_switching_services(client, "https://dds.example.net/dds")
        assert result[0].label_swapping is False

    @pytest.mark.asyncio
    async def test_encoding_and_label_type(self):
        client = make_mock_http(SIMPLE_COLLECTION)
        with patch.dict(dds_client._cache, {}, clear=True):
            result = await dds_client.fetch_switching_services(client, "https://dds.example.net/dds")
        assert result[0].encoding == "http://schemas.ogf.org/nml/2012/10/ethernet"
        assert result[0].label_type == "http://schemas.ogf.org/nml/2012/10/ethernet#vlan"

    @pytest.mark.asyncio
    async def test_topology_id_set_correctly(self):
        client = make_mock_http(SIMPLE_COLLECTION)
        with patch.dict(dds_client._cache, {}, clear=True):
            result = await dds_client.fetch_switching_services(client, "https://dds.example.net/dds")
        assert result[0].topology_id == TOPO_ID

    @pytest.mark.asyncio
    async def test_no_switching_services_returns_empty(self):
        nml = make_nml_topology()
        collection = make_dds_collection([{"id": TOPO_ID, "nml_bytes": nml}])
        client = make_mock_http(collection)
        with patch.dict(dds_client._cache, {}, clear=True):
            result = await dds_client.fetch_switching_services(client, "https://dds.example.net/dds")
        assert result == []


# ---------------------------------------------------------------------------
# fetch_stps
# ---------------------------------------------------------------------------


class TestFetchSTPs:
    @pytest.mark.asyncio
    async def test_returns_stp(self):
        client = make_mock_http(SIMPLE_COLLECTION)
        with patch.dict(dds_client._cache, {}, clear=True):
            result = await dds_client.fetch_stps(client, "https://dds.example.net/dds")
        assert len(result) == 2
        assert any(s.id == PORT_A for s in result)

    @pytest.mark.asyncio
    async def test_stp_name(self):
        client = make_mock_http(SIMPLE_COLLECTION)
        with patch.dict(dds_client._cache, {}, clear=True):
            result = await dds_client.fetch_stps(client, "https://dds.example.net/dds")
        assert result[0].name == "Port One"

    @pytest.mark.asyncio
    async def test_stp_capacity(self):
        client = make_mock_http(SIMPLE_COLLECTION)
        with patch.dict(dds_client._cache, {}, clear=True):
            result = await dds_client.fetch_stps(client, "https://dds.example.net/dds")
        assert result[0].capacity == 100000

    @pytest.mark.asyncio
    async def test_stp_label_group(self):
        client = make_mock_http(SIMPLE_COLLECTION)
        with patch.dict(dds_client._cache, {}, clear=True):
            result = await dds_client.fetch_stps(client, "https://dds.example.net/dds")
        assert result[0].label_group == "100-200"

    @pytest.mark.asyncio
    async def test_stp_switching_service_id(self):
        client = make_mock_http(SIMPLE_COLLECTION)
        with patch.dict(dds_client._cache, {}, clear=True):
            result = await dds_client.fetch_stps(client, "https://dds.example.net/dds")
        assert result[0].switching_service_id == SS_ID

    @pytest.mark.asyncio
    async def test_invalid_capacity_defaults_to_zero(self):
        """A non-integer <eth:capacity> value is caught and the STP gets capacity=0."""
        nml = make_nml_topology(
            ports=[{"id": PORT_A, "name": "Port A", "capacity": "not-a-number", "label_group": "100-200"}]
        )
        collection = make_dds_collection([{"id": TOPO_ID, "nml_bytes": nml}])
        client = make_mock_http(collection)
        with patch.dict(dds_client._cache, {}, clear=True):
            result = await dds_client.fetch_stps(client, "https://dds.example.net/dds")
        assert len(result) == 1
        assert result[0].capacity == 0

    @pytest.mark.asyncio
    async def test_port_with_no_inbound_portgroup_defaults(self):
        """An STP whose BidirectionalPort has no PortGroup children gets capacity=0 and label_group=''."""
        nml = f"""<?xml version="1.0" encoding="UTF-8"?>
<nml:Topology
    id="{TOPO_ID}"
    name="Test"
    xmlns:nml="{NML_NS}"
    xmlns:eth="{ETH_NS}">
  <nml:Lifetime xmlns:nml="{NML_NS}">
    <nml:start xmlns:nml="{NML_NS}">2025-01-01T00:00:00Z</nml:start>
    <nml:end xmlns:nml="{NML_NS}">2026-01-01T00:00:00Z</nml:end>
  </nml:Lifetime>
  <nml:BidirectionalPort id="{PORT_A}" xmlns:nml="{NML_NS}">
    <nml:name xmlns:nml="{NML_NS}">Port A</nml:name>
  </nml:BidirectionalPort>
</nml:Topology>""".encode("utf-8")
        collection = make_dds_collection([{"id": TOPO_ID, "nml_bytes": nml}])
        client = make_mock_http(collection)
        with patch.dict(dds_client._cache, {}, clear=True):
            result = await dds_client.fetch_stps(client, "https://dds.example.net/dds")
        assert len(result) == 1
        assert result[0].id == PORT_A
        assert result[0].capacity == 0
        assert result[0].label_group == ""

    @pytest.mark.asyncio
    async def test_multiple_ports(self):
        port_b = f"{TOPO_ID}:port-2"
        nml = make_nml_topology(
            switching_services=[
                {
                    "id": SS_ID,
                    "encoding": "",
                    "labelSwapping": False,
                    "labelType": "",
                }
            ],
            ports=[
                {"id": PORT_A, "name": "Port A", "capacity": 100000, "label_group": "100-200"},
                {"id": port_b, "name": "Port B", "capacity": 200000, "label_group": "300-400"},
            ],
        )
        collection = make_dds_collection([{"id": TOPO_ID, "nml_bytes": nml}])
        client = make_mock_http(collection)
        with patch.dict(dds_client._cache, {}, clear=True):
            result = await dds_client.fetch_stps(client, "https://dds.example.net/dds")
        assert len(result) == 2
        ids = {s.id for s in result}
        assert PORT_A in ids
        assert port_b in ids


# ---------------------------------------------------------------------------
# fetch_sdps
# ---------------------------------------------------------------------------


class TestFetchSDPs:
    @pytest.mark.asyncio
    async def test_returns_sdp_from_alias(self):
        client = make_mock_http(SIMPLE_COLLECTION)
        with patch.dict(dds_client._cache, {}, clear=True):
            result = await dds_client.fetch_sdps(client, "https://dds.example.net/dds")
        assert len(result) == 1
        stp_ids = {(r.stp_a_id, r.stp_z_id) for r in result}
        assert (PORT_A, PORT_Z) in stp_ids or (PORT_Z, PORT_A) in stp_ids

    @pytest.mark.asyncio
    async def test_no_alias_returns_empty(self):
        nml = make_nml_topology(ports=[{"id": PORT_A, "name": "Port A", "capacity": 0, "label_group": ""}])
        collection = make_dds_collection([{"id": TOPO_ID, "nml_bytes": nml}])
        client = make_mock_http(collection)
        with patch.dict(dds_client._cache, {}, clear=True):
            result = await dds_client.fetch_sdps(client, "https://dds.example.net/dds")
        assert result == []

    @pytest.mark.asyncio
    async def test_one_sided_alias_returns_empty(self):
        nml = make_nml_topology(
            ports=[{"id": PORT_A, "name": "A", "capacity": 0, "label_group": "", "alias_pg_id": PORT_Z_IN}]
        )
        collection = make_dds_collection([{"id": TOPO_ID, "nml_bytes": nml}])
        client = make_mock_http(collection)
        with patch.dict(dds_client._cache, {}, clear=True):
            result = await dds_client.fetch_sdps(client, "https://dds.example.net/dds")
        assert result == []

    @pytest.mark.asyncio
    async def test_unresolved_local_pg_is_skipped(self):
        """A PortGroup in hasInboundPort that has no parent BidirectionalPort is silently skipped."""
        # The hasInboundPort relation contains a PortGroup, but there is no BidirectionalPort
        # in the topology, so pg_to_stp is empty and the lookup returns None.
        nml = f"""<?xml version="1.0" encoding="UTF-8"?>
<nml:Topology
    id="{TOPO_ID}"
    name="Test"
    xmlns:nml="{NML_NS}"
    xmlns:eth="{ETH_NS}">
  <nml:Relation type="http://schemas.ogf.org/nml/2013/05/base#hasInboundPort" xmlns:nml="{NML_NS}">
    <nml:PortGroup id="{PORT_A}:in" xmlns:nml="{NML_NS}">
      <nml:Relation type="http://schemas.ogf.org/nml/2013/05/base#isAlias" xmlns:nml="{NML_NS}">
        <nml:PortGroup id="{PORT_Z}:in" xmlns:nml="{NML_NS}"/>
      </nml:Relation>
    </nml:PortGroup>
  </nml:Relation>
</nml:Topology>""".encode("utf-8")
        collection = make_dds_collection([{"id": TOPO_ID, "nml_bytes": nml}])
        client = make_mock_http(collection)
        with patch.dict(dds_client._cache, {}, clear=True):
            result = await dds_client.fetch_sdps(client, "https://dds.example.net/dds")
        assert result == []

    @pytest.mark.asyncio
    async def test_unresolved_alias_pg_is_skipped(self):
        """An alias PortGroup that belongs to no known BidirectionalPort is silently skipped."""
        orphan_pg_id = "urn:ogf:network:unknown.net:1999:topology:port-orphan:in"
        nml = make_nml_topology(
            ports=[{"id": PORT_A, "name": "A", "capacity": 0, "label_group": "", "alias_pg_id": orphan_pg_id}]
        )
        collection = make_dds_collection([{"id": TOPO_ID, "nml_bytes": nml}])
        client = make_mock_http(collection)
        with patch.dict(dds_client._cache, {}, clear=True):
            result = await dds_client.fetch_sdps(client, "https://dds.example.net/dds")
        assert result == []

    @pytest.mark.asyncio
    async def test_multiple_aliases(self):
        port_b = f"{TOPO_ID}:port-2"
        port_b_in = f"{port_b}:in"
        port_x = f"{TOPO_ID_2}:port-88"
        port_x_in = f"{port_x}:in"
        nml1 = make_nml_topology(
            topo_id=TOPO_ID,
            ports=[
                {"id": PORT_A, "name": "A", "capacity": 0, "label_group": "", "alias_pg_id": PORT_Z_IN},
                {"id": port_b, "name": "B", "capacity": 0, "label_group": "", "alias_pg_id": port_x_in},
            ],
        )
        nml2 = make_nml_topology(
            topo_id=TOPO_ID_2,
            ports=[
                {"id": PORT_Z, "name": "Z", "capacity": 0, "label_group": "", "alias_pg_id": f"{PORT_A}:in"},
                {"id": port_x, "name": "X", "capacity": 0, "label_group": "", "alias_pg_id": port_b_in},
            ],
        )
        collection = make_dds_collection(
            [
                {"id": TOPO_ID, "nml_bytes": nml1},
                {"id": TOPO_ID_2, "nml_bytes": nml2},
            ]
        )
        client = make_mock_http(collection)
        with patch.dict(dds_client._cache, {}, clear=True):
            result = await dds_client.fetch_sdps(client, "https://dds.example.net/dds")
        assert len(result) == 2


# ---------------------------------------------------------------------------
# Caching
# ---------------------------------------------------------------------------


class TestCaching:
    @pytest.mark.asyncio
    async def test_second_call_uses_cache(self):
        client = make_mock_http(SIMPLE_COLLECTION)
        with patch.dict(dds_client._cache, {}, clear=True):
            await dds_client.fetch_topologies(client, "https://dds.example.net/dds")
            await dds_client.fetch_topologies(client, "https://dds.example.net/dds")
        # Only one real HTTP call should have been made
        assert client.get.call_count == 1

    @pytest.mark.asyncio
    async def test_expired_cache_triggers_refetch(self):
        """After the TTL elapses, the cached entry is discarded and a fresh HTTP request is made."""
        client = make_mock_http(SIMPLE_COLLECTION)
        start = 1000.0
        with patch.dict(dds_client._cache, {}, clear=True):
            with patch("dds_proxy.dds_client.time.monotonic", return_value=start):
                await dds_client.fetch_topologies(client, "https://dds.example.net/dds")
            expired = start + settings.cache_ttl_seconds + 1
            with patch("dds_proxy.dds_client.time.monotonic", return_value=expired):
                await dds_client.fetch_topologies(client, "https://dds.example.net/dds")
        assert client.get.call_count == 2

    @pytest.mark.asyncio
    async def test_cache_shared_across_fetch_functions(self):
        """All four fetch_* functions share the same cached topology documents."""
        client = make_mock_http(SIMPLE_COLLECTION)
        with patch.dict(dds_client._cache, {}, clear=True):
            await dds_client.fetch_topologies(client, "https://dds.example.net/dds")
            await dds_client.fetch_switching_services(client, "https://dds.example.net/dds")
            await dds_client.fetch_stps(client, "https://dds.example.net/dds")
            await dds_client.fetch_sdps(client, "https://dds.example.net/dds")
        assert client.get.call_count == 1
