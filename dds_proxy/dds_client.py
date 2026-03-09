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
"""The DDS client.

Fetches the DDS collection, extracts topology documents
(type vnd.ogf.nsi.topology.v2+xml), and parses NML XML into our
Pydantic models.
"""

import base64
import gzip
import time

import httpx
import structlog
from lxml import etree

from dds_proxy.config import settings
from dds_proxy.models import (
    Lifetime,
    ServiceDemarcationPoint,
    ServiceTerminationPoint,
    SwitchingService,
    Topology,
)

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# XML namespaces
# ---------------------------------------------------------------------------

NS = {
    "dds": "http://schemas.ogf.org/nsi/2014/02/discovery/types",
    "nml": "http://schemas.ogf.org/nml/2013/05/base#",
    "nsi": "http://schemas.ogf.org/nsi/2013/12/services/definition",
    "eth": "http://schemas.ogf.org/nml/2012/10/ethernet",
}

TOPOLOGY_CONTENT_TYPE = "vnd.ogf.nsi.topology.v2+xml"

HAS_SERVICE = "http://schemas.ogf.org/nml/2013/05/base#hasService"
HAS_INBOUND_PORT = "http://schemas.ogf.org/nml/2013/05/base#hasInboundPort"
IS_ALIAS = "http://schemas.ogf.org/nml/2013/05/base#isAlias"

# ---------------------------------------------------------------------------
# Simple TTL cache
# ---------------------------------------------------------------------------

TopologyDocuments = list[tuple[etree._Element, etree._Element]]

_cache: dict[str, tuple[float, TopologyDocuments]] = {}


def _cache_get(key: str) -> TopologyDocuments | None:
    if key not in _cache:
        return None
    ts, data = _cache[key]
    ttl = settings.cache_ttl_seconds
    age = time.monotonic() - ts
    if age > ttl:
        log.debug("Cache expired", key=key, age_seconds=round(age, 1), ttl=ttl)
        del _cache[key]
        return None
    log.debug("Cache hit", key=key, age_seconds=round(age, 1), ttl=ttl)
    return data


def _cache_set(key: str, data: TopologyDocuments) -> None:
    _cache[key] = (time.monotonic(), data)
    log.debug("Cache set", key=key)


# ---------------------------------------------------------------------------
# Fetch helpers
# ---------------------------------------------------------------------------


async def _fetch_collection(client: httpx.AsyncClient, dds_base_url: str) -> etree._Element:
    url = f"{dds_base_url.rstrip('/')}/documents"
    log.debug("Fetch DDS collection", url=url)
    response = await client.get(url)
    response.raise_for_status()
    log.debug("DDS collection fetched", url=url, status=response.status_code, bytes=len(response.content))
    return etree.fromstring(response.content)


def _decode_topology_content(content_el: etree._Element) -> etree._Element:
    raw = base64.b64decode(content_el.text.strip())
    xml_bytes = gzip.decompress(raw)
    return etree.fromstring(xml_bytes)


async def _get_topology_documents(
    client: httpx.AsyncClient,
    dds_base_url: str,
) -> list[tuple[etree._Element, etree._Element]]:
    cached = _cache_get("topology_documents")
    if cached is not None:
        log.debug("DDS topology documents from cache", count=len(cached))
        return cached

    collection = await _fetch_collection(client, dds_base_url)
    all_docs = collection.findall(".//dds:document", NS)
    log.debug("DDS collection parsed", total_documents=len(all_docs))

    results = []
    for doc_el in all_docs:
        doc_id = doc_el.get("id", "<unknown>")
        type_el = doc_el.find("type")

        if type_el is None:
            log.debug("DDS document skipped", doc_id=doc_id, reason="no type element")
            continue

        doc_type = type_el.text.strip()
        if doc_type != TOPOLOGY_CONTENT_TYPE:
            log.debug("DDS document skipped", doc_id=doc_id, type=doc_type, reason="not a topology document")
            continue

        log.debug("DDS document processing", doc_id=doc_id)

        content_el = doc_el.find("content")
        if content_el is None or not content_el.text:
            href = doc_el.get("href")
            log.debug("DDS document no inline content", doc_id=doc_id, href=href)
            if href:
                try:
                    resp = await client.get(href)
                    resp.raise_for_status()
                    nml_root = etree.fromstring(resp.content)
                    results.append((doc_el, nml_root))
                    log.debug("DDS document fetched by href", doc_id=doc_id, status=resp.status_code)
                except Exception as exc:
                    log.warning("DDS document href fetch failed", doc_id=doc_id, href=href, error=str(exc))
            continue

        try:
            nml_root = _decode_topology_content(content_el)
            tag = etree.QName(nml_root.tag).localname
            nml_id = nml_root.get("id", "<no id>")
            log.debug("DDS document decoded", doc_id=doc_id, nml_root_tag=tag, nml_id=nml_id)
            results.append((doc_el, nml_root))
        except Exception as exc:
            log.warning("DDS document decode failed", doc_id=doc_id, error=str(exc))

    log.debug("DDS topology documents ready", count=len(results))
    _cache_set("topology_documents", results)
    return results


# ---------------------------------------------------------------------------
# Public parse functions
# ---------------------------------------------------------------------------


async def fetch_topologies(
    client: httpx.AsyncClient,
    dds_base_url: str,
) -> list[Topology]:
    """Fetch topologies from DDS."""
    log.debug("fetch topologies start")
    docs = await _get_topology_documents(client, dds_base_url)
    topologies = []

    for dds_doc, nml_root in docs:
        topo_id = dds_doc.get("id") or nml_root.get("id", "")
        version = dds_doc.get("version", "")
        name_attr = nml_root.get("name", "")
        name_el = nml_root.find("nml:name", NS)
        name = name_attr or (name_el.text.strip() if name_el is not None and name_el.text else topo_id)

        lifetime_el = nml_root.find("nml:Lifetime", NS)
        if lifetime_el is not None:
            start = lifetime_el.findtext("nml:start", default="", namespaces=NS)
            end = lifetime_el.findtext("nml:end", default="", namespaces=NS)
        else:
            log.debug("fetch topologies no lifetime element", topo_id=topo_id, fallback="using dds version/expires")
            start = dds_doc.get("version", "")
            end = dds_doc.get("expires", "")

        log.debug("fetch topologies parsed", topo_id=topo_id, name=name, version=version)
        topologies.append(
            Topology(
                id=topo_id,
                version=version,
                name=name,
                lifetime=Lifetime(start=start, end=end),
            )
        )

    log.debug("Fetch topologies done", count=len(topologies))
    return topologies


async def fetch_switching_services(
    client: httpx.AsyncClient,
    dds_base_url: str,
) -> list[SwitchingService]:
    """Fetch switching services from DDS."""
    log.debug("Fetch switching services start")
    docs = await _get_topology_documents(client, dds_base_url)
    services = []

    for dds_doc, nml_root in docs:
        topology_id = dds_doc.get("id") or nml_root.get("id", "")
        ss_els = nml_root.findall(".//nml:SwitchingService", NS)
        log.debug("Fetch switching services scanning", topology_id=topology_id, found=len(ss_els))

        for ss_el in ss_els:
            ss_id = ss_el.get("id", "")
            encoding = ss_el.get("encoding", "")
            label_swapping = ss_el.get("labelSwapping", "false").lower() == "true"
            label_type = ss_el.get("labelType", "")
            log.debug("Fetch switching services parsed", ss_id=ss_id, encoding=encoding, label_swapping=label_swapping)

            services.append(
                SwitchingService(
                    id=ss_id,
                    encoding=encoding,
                    label_swapping=label_swapping,
                    label_type=label_type,
                    topology_id=topology_id,
                )
            )

    log.debug("Fetch switching services done", count=len(services))
    return services


async def fetch_stps(
    client: httpx.AsyncClient,
    dds_base_url: str,
) -> list[ServiceTerminationPoint]:
    """Fetch Service Termination Points from DDS.

    STPs are the BidirectionalPort elements that are direct children of the
    Topology. Capacity and LabelGroup live on the corresponding inbound
    PortGroup (id = port_id + inbound_suffix) inside the hasInboundPort Relation.
    The SwitchingService id is found via the hasService Relation.
    """
    log.debug("Fetch stps start")
    docs = await _get_topology_documents(client, dds_base_url)
    stps = []

    for _dds_doc, nml_root in docs:
        topo_id = nml_root.get("id", "<unknown>")

        # Build a map of PortGroup id -> PortGroup element from hasInboundPort
        inbound_ports: dict[str, etree._Element] = {}
        for rel_el in nml_root.findall("nml:Relation", NS):
            if rel_el.get("type") == HAS_INBOUND_PORT:
                for pg_el in rel_el.findall("nml:PortGroup", NS):
                    pg_id = pg_el.get("id", "")
                    if pg_id:
                        inbound_ports[pg_id] = pg_el

        log.debug("Fetch stps inbound ports", topo_id=topo_id, count=len(inbound_ports))

        # Find the SwitchingService id via hasService Relation
        ss_id = ""
        for rel_el in nml_root.findall("nml:Relation", NS):
            if rel_el.get("type") == HAS_SERVICE:
                ss_el = rel_el.find("nml:SwitchingService", NS)
                if ss_el is not None:
                    ss_id = ss_el.get("id", "")
                    break

        log.debug("Fetch stps switching service", topo_id=topo_id, ss_id=ss_id)

        # Each direct BidirectionalPort child of Topology is an STP
        bidir_ports = nml_root.findall("nml:BidirectionalPort", NS)
        log.debug("fetch stps bidirectional ports", topo_id=topo_id, count=len(bidir_ports))

        for port_el in bidir_ports:
            port_id = port_el.get("id", "")
            if not port_id:
                continue

            name_el = port_el.find("nml:name", NS)
            name = name_el.text.strip() if name_el is not None and name_el.text else port_id

            # Find any PortGroup child of this BidirectionalPort whose id
            # matches an entry in inbound_ports — avoids assuming a :in suffix
            capacity = 0
            label_group = ""
            pg_el = None

            for pg_ref in port_el.findall("nml:PortGroup", NS):
                ref_id = pg_ref.get("id", "")
                if ref_id in inbound_ports:
                    pg_el = inbound_ports[ref_id]
                    log.debug("Fetch stps inbound portgroup matched", port_id=port_id, pg_id=ref_id)
                    break

            if pg_el is not None:
                cap_el = pg_el.find("eth:capacity", NS)
                if cap_el is not None and cap_el.text:
                    try:
                        capacity = int(cap_el.text.strip())
                    except ValueError:
                        log.warning("Fetch stps invalid capacity", port_id=port_id, raw=cap_el.text)

                lg_el = pg_el.find("nml:LabelGroup", NS)
                if lg_el is not None and lg_el.text:
                    label_group = lg_el.text.strip()
            else:
                log.debug("Fetch stps no inbound portgroup", port_id=port_id)

            log.debug(
                "Fetch stps parsed", port_id=port_id, name=name, capacity=capacity, label_group=label_group, ss_id=ss_id
            )
            stps.append(
                ServiceTerminationPoint(
                    id=port_id,
                    name=name,
                    capacity=capacity,
                    label_group=label_group,
                    switching_service_id=ss_id,
                )
            )

    log.debug("Fetch stps done", count=len(stps))
    return stps


async def fetch_sdps(
    client: httpx.AsyncClient,
    dds_base_url: str,
) -> list[ServiceDemarcationPoint]:
    """Fetch Service Demarcation Points from DDS.

    SDPs are derived from isAlias relations on PortGroup elements inside the
    hasInboundPort and hasOutboundPort Relations. We build a reverse map from
    PortGroup id back to its parent BidirectionalPort id so we don't have to
    make any assumptions about naming conventions.
    """
    log.debug("FetchD sdps start")
    docs = await _get_topology_documents(client, dds_base_url)
    sdps = []

    HAS_PORT_TYPES = {
        "http://schemas.ogf.org/nml/2013/05/base#hasInboundPort",
        "http://schemas.ogf.org/nml/2013/05/base#hasOutboundPort",
    }

    # Build a global reverse map across ALL topologies first so we can
    # resolve PortGroup ids from remote topologies too.
    pg_to_stp: dict[str, str] = {}
    for _dds_doc, nml_root in docs:
        for bidir_el in nml_root.findall("nml:BidirectionalPort", NS):
            stp_id = bidir_el.get("id", "")
            for pg_ref in bidir_el.findall("nml:PortGroup", NS):
                pg_id = pg_ref.get("id", "")
                if pg_id:
                    pg_to_stp[pg_id] = stp_id

    log.debug("Fetch sdps pg to stp map", total_entries=len(pg_to_stp))

    # Collect all declared alias pairs (stp_a, stp_z) across all topologies.
    # A pair only becomes an SDP when both directions are declared.
    declared: set[tuple[str, str]] = set()

    for _dds_doc, nml_root in docs:
        topo_id = nml_root.get("id", "<unknown>")

        for rel_el in nml_root.findall("nml:Relation", NS):
            if rel_el.get("type") not in HAS_PORT_TYPES:
                continue

            for pg_el in rel_el.findall("nml:PortGroup", NS):
                pg_id = pg_el.get("id", "")
                stp_a_id = pg_to_stp.get(pg_id)
                if not stp_a_id:
                    log.debug("Fetch sdps local pg unresolved", pg_id=pg_id, topo_id=topo_id)
                    continue

                for alias_rel in pg_el.findall("nml:Relation", NS):
                    if alias_rel.get("type") != IS_ALIAS:
                        continue

                    for alias_pg in alias_rel.findall("nml:PortGroup", NS):
                        alias_pg_id = alias_pg.get("id", "")
                        if not alias_pg_id:
                            continue

                        stp_z_id = pg_to_stp.get(alias_pg_id)
                        if not stp_z_id:
                            log.debug("Fetch sdps remote pg unresolved", alias_pg_id=alias_pg_id, stp_a_id=stp_a_id)
                            continue

                        declared.add((stp_a_id, stp_z_id))

    log.debug("Fetch sdps declared pairs", count=len(declared))

    # Only emit an SDP when both (A→Z) and (Z→A) are declared.
    seen: set[tuple[str, str]] = set()
    for stp_a_id, stp_z_id in declared:
        if (stp_z_id, stp_a_id) not in declared:
            log.debug("Fetch sdps one sided", stp_a_id=stp_a_id, stp_z_id=stp_z_id)
            continue

        pair = (stp_a_id, stp_z_id)
        reverse = (stp_z_id, stp_a_id)
        if pair not in seen and reverse not in seen:
            seen.add(pair)
            log.debug("Fetch sdps parsed", stp_a_id=stp_a_id, stp_z_id=stp_z_id)
            sdps.append(
                ServiceDemarcationPoint(
                    stp_a_id=stp_a_id,
                    stp_z_id=stp_z_id,
                )
            )

    log.debug("Fetch sdps done", count=len(sdps))
    return sdps
