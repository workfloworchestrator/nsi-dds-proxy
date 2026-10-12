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
(type vnd.ogf.nsi.topology.v2+xml) and NSA discovery documents
(type vnd.ogf.nsi.nsa.v1+xml), and parses them into our Pydantic models.
"""

import base64
import gzip
import time
from itertools import chain

import httpx2
import structlog
from lxml import etree

from dds_proxy.config import settings
from dds_proxy.models import (
    Lifetime,
    NetworkServiceAgent,
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
NSA_CONTENT_TYPE = "vnd.ogf.nsi.nsa.v1+xml"
LOADED_CONTENT_TYPES = {TOPOLOGY_CONTENT_TYPE, NSA_CONTENT_TYPE}

# NSA document interface type of the NSI CS v2 provider endpoint.
CS_PROVIDER_INTERFACE_TYPE = "application/vnd.ogf.nsi.cs.v2.provider+soap"

HAS_SERVICE = "http://schemas.ogf.org/nml/2013/05/base#hasService"
HAS_INBOUND_PORT = "http://schemas.ogf.org/nml/2013/05/base#hasInboundPort"
HAS_OUTBOUND_PORT = "http://schemas.ogf.org/nml/2013/05/base#hasOutboundPort"
IS_ALIAS = "http://schemas.ogf.org/nml/2013/05/base#isAlias"

HAS_PORT_TYPES = {HAS_INBOUND_PORT, HAS_OUTBOUND_PORT}

# ---------------------------------------------------------------------------
# Simple TTL cache
# ---------------------------------------------------------------------------

Documents = list[tuple[etree._Element, etree._Element]]
DocumentsByType = dict[str, Documents]

# Single-slot TTL cache of the loaded documents, grouped by DDS content type.
_cache: tuple[float, DocumentsByType] | None = None


def _cache_get() -> DocumentsByType | None:
    if _cache is None:
        return None
    ts, data = _cache
    ttl = settings.cache_ttl_seconds
    age = time.monotonic() - ts
    if age > ttl:
        log.debug("Cache expired", age_seconds=round(age, 1), ttl=ttl)
        return None
    log.debug("Cache hit", age_seconds=round(age, 1), ttl=ttl)
    return data


def _cache_set(data: DocumentsByType) -> None:
    global _cache
    _cache = (time.monotonic(), data)
    log.debug("Cache set")


# ---------------------------------------------------------------------------
# Fetch helpers
# ---------------------------------------------------------------------------


async def _fetch_collection(client: httpx2.AsyncClient, dds_base_url: str) -> etree._Element:
    url = f"{dds_base_url.rstrip('/')}/documents"
    log.debug("Fetch DDS collection", url=url)
    response = await client.get(url)
    response.raise_for_status()
    log.debug("DDS collection fetched", url=url, status=response.status_code, bytes=len(response.content))
    return etree.fromstring(response.content)


def _document_type(doc_el: etree._Element) -> str:
    """The DDS ``<document>``'s content type, or '' when it has none."""
    return (doc_el.findtext("type") or "").strip()


def _decode_content(content_el: etree._Element) -> etree._Element:
    raw = base64.b64decode((content_el.text or "").strip())
    xml_bytes = gzip.decompress(raw)
    return etree.fromstring(xml_bytes)


async def _load_document_by_href(
    client: httpx2.AsyncClient, doc_el: etree._Element
) -> tuple[etree._Element, etree._Element] | None:
    """Fetch a document's XML from its ``href`` when there is no inline content."""
    doc_id = doc_el.get("id", "<unknown>")
    href = doc_el.get("href")
    log.debug("DDS document no inline content", doc_id=doc_id, href=href)
    if not href:
        return None
    try:
        resp = await client.get(href)
        resp.raise_for_status()
        root = etree.fromstring(resp.content)
        log.debug("DDS document fetched by href", doc_id=doc_id, status=resp.status_code)
        return (doc_el, root)
    except Exception as exc:
        log.warning("DDS document href fetch failed", doc_id=doc_id, href=href, error=str(exc))
        return None


async def _load_document(
    client: httpx2.AsyncClient, doc_el: etree._Element
) -> tuple[etree._Element, etree._Element] | None:
    """Parse one DDS ``<document>`` into ``(doc_el, root)``, or None if it should be skipped."""
    doc_id = doc_el.get("id", "<unknown>")
    doc_type = _document_type(doc_el)
    if doc_type not in LOADED_CONTENT_TYPES:
        log.debug("DDS document skipped", doc_id=doc_id, type=doc_type, reason="not a topology or NSA document")
        return None

    log.debug("DDS document processing", doc_id=doc_id, type=doc_type)

    content_el = doc_el.find("content")
    if content_el is None or not content_el.text:
        return await _load_document_by_href(client, doc_el)

    try:
        root = _decode_content(content_el)
        log.debug(
            "DDS document decoded",
            doc_id=doc_id,
            root_tag=etree.QName(root.tag).localname,
            root_id=root.get("id", "<no id>"),
        )
        return (doc_el, root)
    except Exception as exc:
        log.warning("DDS document decode failed", doc_id=doc_id, error=str(exc))
        return None


async def _get_documents(client: httpx2.AsyncClient, dds_base_url: str) -> DocumentsByType:
    cached = _cache_get()
    if cached is not None:
        log.debug("DDS documents from cache", count=sum(map(len, cached.values())))
        return cached

    collection = await _fetch_collection(client, dds_base_url)
    all_docs = collection.findall(".//dds:document", NS)
    log.debug("DDS collection parsed", total_documents=len(all_docs))

    results = []
    for doc_el in all_docs:
        loaded = await _load_document(client, doc_el)
        if loaded is not None:
            results.append(loaded)

    log.debug("DDS documents ready", count=len(results))
    by_type = {
        doc_type: [doc for doc in results if _document_type(doc[0]) == doc_type] for doc_type in LOADED_CONTENT_TYPES
    }
    _cache_set(by_type)
    return by_type


async def _get_documents_of_type(client: httpx2.AsyncClient, dds_base_url: str, doc_type: str) -> Documents:
    """The loaded documents whose DDS content type is ``doc_type``."""
    return (await _get_documents(client, dds_base_url))[doc_type]


async def _get_topology_documents(client: httpx2.AsyncClient, dds_base_url: str) -> Documents:
    return await _get_documents_of_type(client, dds_base_url, TOPOLOGY_CONTENT_TYPE)


def _collect_inbound_ports(nml_root: etree._Element) -> dict[str, etree._Element]:
    """Build a map of PortGroup id -> PortGroup element from hasInboundPort Relations."""
    return {
        pg_item.get("id", ""): pg_item
        for pg_item in chain.from_iterable(
            rel_el.findall("nml:PortGroup", NS)
            for rel_el in nml_root.findall("nml:Relation", NS)
            if rel_el.get("type") == HAS_INBOUND_PORT
        )
        if pg_item.get("id", "")
    }


def _find_matching_inbound_port(
    port_el: etree._Element,
    inbound_ports: dict[str, etree._Element],
) -> etree._Element | None:
    """Find the inbound PortGroup that matches a BidirectionalPort's child PortGroup."""
    for pg_ref in port_el.findall("nml:PortGroup", NS):
        ref_id = pg_ref.get("id", "")
        if ref_id in inbound_ports:
            log.debug("fetch stps inbound portgroup matched", port_id=port_el.get("id", ""), pg_id=ref_id)
            return inbound_ports[ref_id]
    return None


def _build_pg_to_stp_map(docs: Documents) -> dict[str, str]:
    """Build a reverse map from PortGroup id to parent BidirectionalPort id across all topologies."""
    return {
        pg_id: stp_id
        for _dds_doc, nml_root in docs
        for bidir_el in nml_root.findall("nml:BidirectionalPort", NS)
        for stp_id in [bidir_el.get("id", "")]
        for pg_ref in bidir_el.findall("nml:PortGroup", NS)
        for pg_id in [pg_ref.get("id", "")]
        if pg_id
    }


def _resolve_alias(
    alias_pg: etree._Element,
    stp_a_id: str,
    pg_to_stp: dict[str, str],
) -> tuple[str, str] | None:
    """Resolve one isAlias PortGroup to an (stp_a, stp_z) pair, or None if unresolved."""
    alias_pg_id = alias_pg.get("id", "")
    if not alias_pg_id:
        return None
    stp_z_id = pg_to_stp.get(alias_pg_id)
    if not stp_z_id:
        log.debug("fetch sdps remote pg unresolved", alias_pg_id=alias_pg_id, stp_a_id=stp_a_id)
        return None
    return (stp_a_id, stp_z_id)


def _collect_alias_pairs(
    pg_el: etree._Element,
    stp_a_id: str,
    pg_to_stp: dict[str, str],
) -> list[tuple[str, str]]:
    """Extract (stp_a, stp_z) alias pairs from a PortGroup's isAlias Relations."""
    alias_pgs = chain.from_iterable(
        alias_rel.findall("nml:PortGroup", NS)
        for alias_rel in pg_el.findall("nml:Relation", NS)
        if alias_rel.get("type") == IS_ALIAS
    )
    resolved = (_resolve_alias(alias_pg, stp_a_id, pg_to_stp) for alias_pg in alias_pgs)
    return [pair for pair in resolved if pair is not None]


def _parse_stp(
    port_el: etree._Element,
    inbound_ports: dict[str, etree._Element],
    ss_id: str,
) -> ServiceTerminationPoint | None:
    """Parse a single BidirectionalPort element into a ServiceTerminationPoint."""
    port_id = port_el.get("id", "")
    if not port_id:
        return None

    name_el = port_el.find("nml:name", NS)
    name = name_el.text.strip() if name_el is not None and name_el.text else port_id

    capacity = 0
    label_group = ""
    pg_el = _find_matching_inbound_port(port_el, inbound_ports)

    if pg_el is not None:
        cap_el = pg_el.find("eth:capacity", NS)
        if cap_el is not None and cap_el.text:
            try:
                capacity = int(cap_el.text.strip())
            except ValueError:
                log.warning("fetch stps invalid capacity", port_id=port_id, raw=cap_el.text)

        lg_el = pg_el.find("nml:LabelGroup", NS)
        if lg_el is not None and lg_el.text:
            label_group = lg_el.text.strip()
    else:
        log.debug("fetch stps no inbound portgroup", port_id=port_id)

    log.debug(
        "fetch stps parsed", port_id=port_id, name=name, capacity=capacity, label_group=label_group, ss_id=ss_id
    )
    return ServiceTerminationPoint(
        id=port_id,
        name=name,
        capacity=capacity,
        label_group=label_group,
        switching_service_id=ss_id,
    )


# ---------------------------------------------------------------------------
# Public parse functions
# ---------------------------------------------------------------------------


async def fetch_topologies(
    client: httpx2.AsyncClient,
    dds_base_url: str,
) -> list[Topology]:
    """Fetch topologies from DDS."""
    log.debug("fetch topologies start")
    docs = await _get_topology_documents(client, dds_base_url)
    topologies = []

    for dds_doc, nml_root in docs:
        topo_id = dds_doc.get("id") or nml_root.get("id") or ""
        version = dds_doc.get("version", "")
        name_attr = nml_root.get("name", "")
        name_el = nml_root.find("nml:name", NS)
        name = name_attr or (name_el.text.strip() if name_el is not None and name_el.text else "") or topo_id

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

    log.debug("fetch topologies done", count=len(topologies))
    return topologies


def _parse_switching_service(ss_el: etree._Element, topology_id: str) -> SwitchingService:
    """Parse a single SwitchingService element."""
    ss_id = ss_el.get("id", "")
    encoding = ss_el.get("encoding", "")
    label_swapping = ss_el.get("labelSwapping", "false").lower() == "true"
    log.debug("fetch switching services parsed", ss_id=ss_id, encoding=encoding, label_swapping=label_swapping)
    return SwitchingService(
        id=ss_id,
        encoding=encoding,
        label_swapping=label_swapping,
        label_type=ss_el.get("labelType", ""),
        topology_id=topology_id,
    )


def _switching_services_for_document(dds_doc: etree._Element, nml_root: etree._Element) -> list[SwitchingService]:
    """Parse every SwitchingService in one topology document."""
    topology_id = dds_doc.get("id") or nml_root.get("id") or ""
    ss_els = nml_root.findall(".//nml:SwitchingService", NS)
    log.debug("fetch switching services scanning", topology_id=topology_id, found=len(ss_els))
    return [_parse_switching_service(ss_el, topology_id) for ss_el in ss_els]


async def fetch_switching_services(
    client: httpx2.AsyncClient,
    dds_base_url: str,
) -> list[SwitchingService]:
    """Fetch switching services from DDS."""
    log.debug("fetch switching services start")
    docs = await _get_topology_documents(client, dds_base_url)
    services = list(
        chain.from_iterable(_switching_services_for_document(dds_doc, nml_root) for dds_doc, nml_root in docs)
    )
    log.debug("fetch switching services done", count=len(services))
    return services


def _first_switching_service_id(nml_root: etree._Element) -> str:
    """Return the SwitchingService id from the first hasService Relation, or '' if none."""
    service_els = (
        rel_el.find("nml:SwitchingService", NS)
        for rel_el in nml_root.findall("nml:Relation", NS)
        if rel_el.get("type") == HAS_SERVICE
    )
    ss_el = next((el for el in service_els if el is not None), None)
    return ss_el.get("id", "") if ss_el is not None else ""


def _stps_for_document(nml_root: etree._Element) -> list[ServiceTerminationPoint]:
    """Parse every BidirectionalPort in one topology into a ServiceTerminationPoint."""
    topo_id = nml_root.get("id", "<unknown>")
    inbound_ports = _collect_inbound_ports(nml_root)
    log.debug("fetch stps inbound ports", topo_id=topo_id, count=len(inbound_ports))

    ss_id = _first_switching_service_id(nml_root)
    log.debug("fetch stps switching service", topo_id=topo_id, ss_id=ss_id)

    bidir_ports = nml_root.findall("nml:BidirectionalPort", NS)
    log.debug("fetch stps bidirectional ports", topo_id=topo_id, count=len(bidir_ports))

    parsed = (_parse_stp(port_el, inbound_ports, ss_id) for port_el in bidir_ports)
    return [stp for stp in parsed if stp is not None]


async def fetch_stps(
    client: httpx2.AsyncClient,
    dds_base_url: str,
) -> list[ServiceTerminationPoint]:
    """Fetch Service Termination Points from DDS.

    STPs are the BidirectionalPort elements that are direct children of the
    Topology. Capacity and LabelGroup live on the corresponding inbound
    PortGroup (id = port_id + inbound_suffix) inside the hasInboundPort Relation.
    The SwitchingService id is found via the hasService Relation.
    """
    log.debug("fetch stps start")
    docs = await _get_topology_documents(client, dds_base_url)
    stps = list(chain.from_iterable(_stps_for_document(nml_root) for _dds_doc, nml_root in docs))
    log.debug("fetch stps done", count=len(stps))
    return stps


def _declared_pairs_for_document(nml_root: etree._Element, pg_to_stp: dict[str, str]) -> set[tuple[str, str]]:
    """Collect declared (stp_a, stp_z) alias pairs from one topology's in/outbound PortGroups."""
    topo_id = nml_root.get("id", "<unknown>")
    port_groups = chain.from_iterable(
        rel_el.findall("nml:PortGroup", NS)
        for rel_el in nml_root.findall("nml:Relation", NS)
        if rel_el.get("type") in HAS_PORT_TYPES
    )
    pairs: set[tuple[str, str]] = set()
    for pg_el in port_groups:
        stp_a_id = pg_to_stp.get(pg_el.get("id", ""))
        if stp_a_id:
            pairs.update(_collect_alias_pairs(pg_el, stp_a_id, pg_to_stp))
        else:
            log.debug("fetch sdps local pg unresolved", pg_id=pg_el.get("id", ""), topo_id=topo_id)
    return pairs


def _dedupe_bidirectional(declared: set[tuple[str, str]]) -> list[ServiceDemarcationPoint]:
    """Emit one SDP per unordered STP pair that is declared in both directions."""
    seen: set[tuple[str, str]] = set()
    sdps: list[ServiceDemarcationPoint] = []
    for stp_a_id, stp_z_id in declared:
        if (stp_z_id, stp_a_id) not in declared:
            log.debug("fetch sdps one sided", stp_a_id=stp_a_id, stp_z_id=stp_z_id)
        elif (stp_a_id, stp_z_id) not in seen and (stp_z_id, stp_a_id) not in seen:
            seen.add((stp_a_id, stp_z_id))
            log.debug("fetch sdps parsed", stp_a_id=stp_a_id, stp_z_id=stp_z_id)
            sdps.append(ServiceDemarcationPoint(stp_a_id=stp_a_id, stp_z_id=stp_z_id))
    return sdps


async def fetch_sdps(
    client: httpx2.AsyncClient,
    dds_base_url: str,
) -> list[ServiceDemarcationPoint]:
    """Fetch Service Demarcation Points from DDS.

    SDPs are derived from isAlias relations on PortGroup elements inside the
    hasInboundPort and hasOutboundPort Relations. We build a reverse map from
    PortGroup id back to its parent BidirectionalPort id so we don't have to
    make any assumptions about naming conventions.
    """
    log.debug("fetch sdps start")
    docs = await _get_topology_documents(client, dds_base_url)

    pg_to_stp = _build_pg_to_stp_map(docs)
    log.debug("fetch sdps pg to stp map", total_entries=len(pg_to_stp))

    # A pair only becomes an SDP when both directions are declared.
    declared = set(
        chain.from_iterable(_declared_pairs_for_document(nml_root, pg_to_stp) for _dds_doc, nml_root in docs)
    )
    log.debug("fetch sdps declared pairs", count=len(declared))

    sdps = _dedupe_bidirectional(declared)
    log.debug("fetch sdps done", count=len(sdps))
    return sdps


def _child_texts(nsa_root: etree._Element, tag: str) -> list[str]:
    """The stripped, non-empty texts of every ``tag`` child, in either the NSA or no namespace."""
    return [text for el in nsa_root.findall(f"{{*}}{tag}") if (text := (el.text or "").strip())]


def _provider_url(nsa_root: etree._Element) -> str | None:
    """The href of the NSA's first NSI CS v2 provider interface, as nsi-pce selects it."""
    hrefs = (
        (interface.findtext("{*}href") or "").strip()
        for interface in nsa_root.findall("{*}interface")
        if (interface.findtext("{*}type") or "").strip().lower() == CS_PROVIDER_INTERFACE_TYPE
    )
    return next((href for href in hrefs if href), None)


def _parse_nsa(dds_doc: etree._Element, nsa_root: etree._Element) -> NetworkServiceAgent:
    """Parse one NSA discovery document."""
    nsa_id = nsa_root.get("id") or dds_doc.get("id") or ""
    name = (nsa_root.findtext("{*}name") or "").strip() or nsa_id
    roles = [feature_type for el in nsa_root.findall("{*}feature") if (feature_type := (el.get("type") or "").strip())]
    log.debug("fetch network service agents parsed", nsa_id=nsa_id, roles=roles)
    return NetworkServiceAgent(
        id=nsa_id,
        name=name,
        provider_url=_provider_url(nsa_root),
        network_ids=_child_texts(nsa_root, "networkId"),
        roles=roles,
        peers_with=_child_texts(nsa_root, "peersWith"),
    )


async def fetch_network_service_agents(
    client: httpx2.AsyncClient,
    dds_base_url: str,
) -> list[NetworkServiceAgent]:
    """Fetch the Network Service Agents from their DDS discovery documents."""
    log.debug("fetch network service agents start")
    docs = await _get_documents_of_type(client, dds_base_url, NSA_CONTENT_TYPE)
    agents = [_parse_nsa(dds_doc, nsa_root) for dds_doc, nsa_root in docs]
    log.debug("fetch network service agents done", count=len(agents))
    return agents
