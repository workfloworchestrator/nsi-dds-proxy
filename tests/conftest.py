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
"""Shared fixtures and NML XML builders for the dds-proxy test suite."""

import base64
import gzip
from unittest.mock import patch

import pytest

from dds_proxy import dds_client
from dds_proxy.dds_client import IS_ALIAS, NS, TOPOLOGY_CONTENT_TYPE

# ---------------------------------------------------------------------------
# Namespace constants
# ---------------------------------------------------------------------------

DDS_NS = NS["dds"]
NML_NS = NS["nml"]
ETH_NS = NS["eth"]
ALIAS = IS_ALIAS
TOPO_TYPE = TOPOLOGY_CONTENT_TYPE


# ---------------------------------------------------------------------------
# XML builders
# ---------------------------------------------------------------------------


def make_nml_topology(
    topo_id: str = "urn:ogf:network:example.net:2020:topology",
    name: str = "Example Topology",
    start: str = "2025-01-01T00:00:00Z",
    end: str = "2026-01-01T00:00:00Z",
    switching_services: list[dict] | None = None,
    ports: list[dict] | None = None,
) -> bytes:
    """Build a minimal NML topology XML document matching the real DDS structure.

    switching_services: list of dicts with keys:
        id, encoding, labelSwapping, labelType

    ports: list of dicts with keys:
        id, name, capacity, label_group
        inbound_pg_id   (id of the inbound PortGroup, defaults to id + ":in")
        outbound_pg_id  (id of the outbound PortGroup, defaults to id + ":out")
        alias_pg_id     (optional: PortGroup id in a remote topology to alias to)
    """
    # SwitchingService wrapped in a hasService Relation
    ss_xml = ""
    for ss in switching_services or []:
        ss_xml += f"""
  <nml:Relation type="http://schemas.ogf.org/nml/2013/05/base#hasService" xmlns:nml="{NML_NS}">
    <nml:SwitchingService
        id="{ss["id"]}"
        encoding="{ss.get("encoding", "")}"
        labelSwapping="{str(ss.get("labelSwapping", False)).lower()}"
        labelType="{ss.get("labelType", "")}"
        xmlns:nml="{NML_NS}"/>
  </nml:Relation>"""

    # BidirectionalPort elements (direct Topology children)
    bidir_xml = ""
    inbound_pg_xml = ""
    outbound_pg_xml = ""

    for p in ports or []:
        in_id = p.get("inbound_pg_id", f"{p['id']}:in")
        out_id = p.get("outbound_pg_id", f"{p['id']}:out")

        bidir_xml += f"""
  <nml:BidirectionalPort id="{p["id"]}" xmlns:nml="{NML_NS}">
    <nml:name xmlns:nml="{NML_NS}">{p.get("name", p["id"])}</nml:name>
    <nml:PortGroup id="{in_id}" xmlns:nml="{NML_NS}"/>
    <nml:PortGroup id="{out_id}" xmlns:nml="{NML_NS}"/>
  </nml:BidirectionalPort>"""

        alias_inbound_xml = ""
        alias_outbound_xml = ""
        if p.get("alias_pg_id"):
            alias_inbound_xml = f"""
      <nml:Relation type="{ALIAS}" xmlns:nml="{NML_NS}">
        <nml:PortGroup id="{p["alias_pg_id"]}" xmlns:nml="{NML_NS}"/>
      </nml:Relation>"""
            # mirror alias on outbound too so bidirectional check passes in tests
            alias_outbound_xml = alias_inbound_xml

        inbound_pg_xml += f"""
    <nml:PortGroup id="{in_id}" encoding="{p.get("encoding", "")}" xmlns:nml="{NML_NS}">
      <nml:LabelGroup xmlns:nml="{NML_NS}">{p.get("label_group", "")}</nml:LabelGroup>
      <eth:capacity xmlns:eth="{ETH_NS}">{p.get("capacity", 0)}</eth:capacity>{alias_inbound_xml}
    </nml:PortGroup>"""

        outbound_pg_xml += f"""
    <nml:PortGroup id="{out_id}" encoding="{p.get("encoding", "")}" xmlns:nml="{NML_NS}">
      <nml:LabelGroup xmlns:nml="{NML_NS}">{p.get("label_group", "")}</nml:LabelGroup>
      <eth:capacity xmlns:eth="{ETH_NS}">{p.get("capacity", 0)}</eth:capacity>{alias_outbound_xml}
    </nml:PortGroup>"""

    has_inbound = (
        f"""
  <nml:Relation type="http://schemas.ogf.org/nml/2013/05/base#hasInboundPort" xmlns:nml="{NML_NS}">{inbound_pg_xml}
  </nml:Relation>"""
        if inbound_pg_xml
        else ""
    )

    has_outbound = (
        f"""
  <nml:Relation type="http://schemas.ogf.org/nml/2013/05/base#hasOutboundPort" xmlns:nml="{NML_NS}">{outbound_pg_xml}
  </nml:Relation>"""
        if outbound_pg_xml
        else ""
    )

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<nml:Topology
    id="{topo_id}"
    name="{name}"
    xmlns:nml="{NML_NS}"
    xmlns:eth="{ETH_NS}">
  <nml:Lifetime xmlns:nml="{NML_NS}">
    <nml:start xmlns:nml="{NML_NS}">{start}</nml:start>
    <nml:end xmlns:nml="{NML_NS}">{end}</nml:end>
  </nml:Lifetime>{ss_xml}{bidir_xml}{has_inbound}{has_outbound}
</nml:Topology>""".encode("utf-8")


def gzip_b64(data: bytes) -> str:
    return base64.b64encode(gzip.compress(data)).decode("ascii")


def make_dds_collection(topologies: list[dict]) -> bytes:
    """Build a DDS collection XML document.

    topologies: list of dicts with keys:
        id, version, expires, nml_bytes (raw NML XML bytes)
    """
    docs_xml = ""
    for t in topologies:
        encoded = gzip_b64(t["nml_bytes"])
        docs_xml += f"""
        <ns0:document
            id="{t["id"]}"
            href="https://dds.example.net/dds/documents/{t["id"]}"
            version="{t.get("version", "2026-01-01T00:00:00Z")}"
            expires="{t.get("expires", "2026-12-31T00:00:00Z")}"
            xmlns:ns0="{DDS_NS}">
          <nsa>urn:ogf:network:example.net:2020:nsa</nsa>
          <type>{TOPO_TYPE}</type>
          <content contentType="application/x-gzip" contentTransferEncoding="base64">{encoded}</content>
        </ns0:document>"""

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<ns0:collection xmlns:ns0="{DDS_NS}">
  <ns0:documents>{docs_xml}</ns0:documents>
</ns0:collection>""".encode("utf-8")


# ---------------------------------------------------------------------------
# Reusable test data
# ---------------------------------------------------------------------------

TOPO_ID = "urn:ogf:network:example.net:2020:topology"
TOPO_ID_2 = "urn:ogf:network:other.net:2021:topology"
SS_ID = f"{TOPO_ID}:switch:EVTS.ANA"
SS_ID_2 = f"{TOPO_ID_2}:switch:EVTS.ANA"
PORT_A = f"{TOPO_ID}:port-1"
PORT_A_IN = f"{PORT_A}:in"
PORT_A_OUT = f"{PORT_A}:out"
PORT_Z = f"{TOPO_ID_2}:port-99"
PORT_Z_IN = f"{PORT_Z}:in"
PORT_Z_OUT = f"{PORT_Z}:out"

# Two topologies with matching bidirectional aliases so SDPs are emitted
SIMPLE_NML = make_nml_topology(
    topo_id=TOPO_ID,
    name="Example Network",
    switching_services=[
        {
            "id": SS_ID,
            "encoding": "http://schemas.ogf.org/nml/2012/10/ethernet",
            "labelSwapping": True,
            "labelType": "http://schemas.ogf.org/nml/2012/10/ethernet#vlan",
        }
    ],
    ports=[
        {
            "id": PORT_A,
            "name": "Port One",
            "capacity": 100000,
            "label_group": "100-200",
            "alias_pg_id": PORT_Z_IN,
        }
    ],
)

SIMPLE_NML_2 = make_nml_topology(
    topo_id=TOPO_ID_2,
    name="Other Network",
    switching_services=[
        {
            "id": SS_ID_2,
            "encoding": "http://schemas.ogf.org/nml/2012/10/ethernet",
            "labelSwapping": False,
            "labelType": "http://schemas.ogf.org/nml/2012/10/ethernet#vlan",
        }
    ],
    ports=[
        {
            "id": PORT_Z,
            "name": "Port Z",
            "capacity": 200000,
            "label_group": "200-300",
            "alias_pg_id": PORT_A_IN,
        }
    ],
)

SIMPLE_COLLECTION = make_dds_collection(
    [
        {"id": TOPO_ID, "nml_bytes": SIMPLE_NML},
        {"id": TOPO_ID_2, "nml_bytes": SIMPLE_NML_2},
    ]
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def clear_cache():
    """Ensure every test starts with an empty DDS cache."""
    with patch.dict(dds_client._cache, {}, clear=True):
        yield
