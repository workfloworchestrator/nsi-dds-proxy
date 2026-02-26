# nsi-dds-proxy

The NSI Document Distribution Service proxy offers a REST API to retrieve
topologies, switching services, service termination points, and service
demarcation points from the combined topology documents found on the DDS.  The
information returned is a subset as needed by NSI ultimate Requester Agents
like the NSI Orchestrator, SENSE, and others.

## API Endpoints

### GET /topologies

Get a list of topologies found in DDS.

#### Response

```json
[
  {
    "id": "urn:ogf:network:example.domain.toplevel:2020:topology",
    "version": "2025-10-18 17:45 00:00",
    "name": "example.domain topology",
    "Lifetime": {
      "start": "2025-12-11T22:13:01+00:00",
      "end": "2025-12-18T22:13:01+00:00"
    },
  },
  ...
]
```

### GET /switching-services

Get a list of switching services found in all topologies found in DDS.

#### Response

```json
[
  {
    "id": "urn:ogf:network:example.domain.toplevel:2020:topology:switch:EVTS.ANA",
    "encoding": "http://schemas.ogf.org/nml/2012/10/ethernet",
    "labelSwapping": "true",
    "labelType": "http://schemas.ogf.org/nml/2012/10/ethernet#vlan",
    "topologyId": "urn:ogf:network:example.domain.toplevel:2020:topology"
  },
  ...
]
```

### GET /service-termination-points

Get a list of STP attached to all switching services found in all topologies.

#### Response

```json
[
  {
    "id": "urn:ogf:network:example.domain.toplevel:2020:topology:ps1",
    "name": "perfSONAR node 1",
    "capacity": 400000,
    "LabelGroup": "2100-2400,3100-3400",
    "SwitchingServiceId": "urn:ogf:network:example.domain.toplevel:2020:topology:switch:EVTS.ANA"
  },
  ...
]
```

### GET /service-demarcation-points

Get a list of SDP consisting of matching STP attached to all switching services
found in all topologies.

```json
[
  {
    "StpAId": "urn:ogf:network:example.domain.toplevel:2020:topology:ps1",
    "StpZId": "urn:ogf:network:another.domain.toplevel:1999:topology:data-center-3"
  },
  ...
]
```
