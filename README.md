# nsi-dds-proxy

The NSI Document Distribution Service proxy offers a REST API to retrieve
topologies, switching services, service termination points, and service
demarcation points from the combined topology documents found on the DDS.  The
information returned is a subset as needed by NSI ultimate Requester Agents
like the NSI Orchestrator, SENSE, and others.

## Project ANA-GRAM

This software is being developed by the 
[Advanced North-Atlantic Consortium](https://www.anaeng.global/), 
a cooperation between National Education and Research Networks (NRENs) and 
research partners to provide network connectivity for research and education 
across the North-Atlantic, as part of the ANA-GRAM (ANA Global Resource Aggregation Method) project. 

The goal of the ANA-GRAM project is to federate the ANA trans-Atlantic links through
[Network Service Interface (NSI)](https://ogf.org/documents/GFD.237.pdf)-based automation.
This will enable the automated provisioning of L2 circuits spanning different domains 
between research parties on other sides of the Atlantic. The ANA-GRAM project is 
spearheaded by the ANA Platform & Requirements Working Group, under guidance of the 
ANA Engineering and ANA Planning Groups.  

<p align="center" width="50%">
    <img width="50%" src="/artwork/ana-logo-scaled-ab2.png">
</p>

## Architecture

The diagram below shows the ANA-GRAM automation stack and how the DDS Proxy fits into the broader architecture.

<p align="center">
    <img src="/artwork/ana-automation-stack.drawio.svg">
</p>

**Color legend:**

| Color | Meaning |
|-------|---------|
| Purple | Existing software deployed in every participating network |
| Green | Existing NSI infrastructure software |
| Orange | Software being developed as part of ANA-GRAM |
| Yellow | Future software to be developed as part of ANA-GRAM |

**Components:**

- [**ANA Frontend**](https://github.com/workfloworchestrator) — Future management portal that will provide a comprehensive overview of all configured services on the ANA infrastructure, including real-time operational status information.
- [**NSI Orchestrator**](https://github.com/workfloworchestrator/nsi-orchestrator) — Central orchestration layer that manages the lifecycle of topologies, switching services, STPs, SDPs, and multi-domain connections. It uses the DDS Proxy for topology visibility and the NSI Aggregator Proxy as its Network Resource Manager.
- [**DDS Proxy**](https://github.com/workfloworchestrator/nsi-dds-proxy) (this repository) — Fetches NML topology documents from the upstream DDS, parses them, and exposes the data as a JSON REST API.
- [**NSI Aggregator Proxy**](https://github.com/workfloworchestrator/nsi-aggregator-proxy) — Translates simple REST/JSON calls into NSI Connection Service v2 SOAP messages toward the NSI Aggregator, abstracting NSI protocol complexity behind a linear state machine.
- [**DDS**](https://github.com/BandwidthOnDemand/nsi-dds) — The NSI Document Distribution Service, a distributed registry where networks publish and discover NML topology documents and NSA descriptions.
- [**PCE**](https://github.com/BandwidthOnDemand/nsi-pce) — The NSI Path Computation Element, which computes end-to-end paths across multiple network domains using topology information from the DDS.
- [**NSI Aggregator (Safnari)**](https://github.com/BandwidthOnDemand/nsi-safnari) — An NSI Connection Service v2.1 Aggregator that coordinates connection requests across multiple provider domains, using the PCE for path computation.
- [**SuPA**](https://github.com/workfloworchestrator/SuPA) — The SURF ultimate Provider Agent, an NSI Provider Agent that manages circuit reservation, creation, and removal within a single network domain. Uses gRPC instead of SOAP, and is always deployed together with [**PolyNSI**](https://github.com/workfloworchestrator/PolyNSI), a bidirectional SOAP-to-gRPC translation proxy.

## Prerequisites

- A valid client certificate and private key for mutual TLS authentication with the DDS server.
- Python 3.13+ (for running from source) or Docker.

## Configuration

All settings can be configured via environment variables or a `dds_proxy.env` file placed in the working directory. Environment variables take precedence over the env file.

| Variable | Default | Description |
|---|---|---|
| `DDS_BASE_URL` | `https://your-dds-server/dds` | Base URL of the upstream DDS server. |
| `DDS_CLIENT_CERT` | _(unset)_ | Path to the PEM-encoded client certificate used for mutual TLS with the DDS server. |
| `DDS_CLIENT_KEY` | _(unset)_ | Path to the PEM-encoded private key corresponding to the client certificate. |
| `DDS_CA_BUNDLE` | _(unset)_ | Path to a PEM file containing the CA certificates used to verify the DDS server. When set, replaces the system CA store entirely. |
| `CACHE_TTL_SECONDS` | `60` | How long (in seconds) the DDS response is cached before the next upstream fetch. |
| `HTTP_TIMEOUT_SECONDS` | `30.0` | Timeout (in seconds) for HTTP requests to the DDS server. |
| `LOG_LEVEL` | `INFO` | Logging verbosity. Accepted values: `DEBUG`, `INFO`, `WARNING`, `ERROR`. |
| `DDS_PROXY_HOST` | `localhost` | Interface the server binds to. Set to `0.0.0.0` to accept connections on all interfaces. |
| `DDS_PROXY_PORT` | `8000` | TCP port the server listens on. |
| `ROOT_PATH` | _(empty)_ | ASGI root path prefix. Set when serving behind a reverse proxy that strips a path prefix (e.g. `/dds-proxy`). Ensures Swagger UI loads the OpenAPI spec from the correct URL. Does not affect route matching. |

### Authentication (optional)

The DDS Proxy supports two authentication methods: **OIDC** (JWT from oauth2-proxy) and **mTLS** (header from auth subrequest service). Authentication is **disabled by default**. When enabled, every request to data endpoints must be authenticated via at least one method; requests without valid credentials are rejected with 401.

#### Architecture

Two separate nginx ingresses protect the DDS Proxy API — one for **mTLS** (machine clients) and one for **OIDC** (browser users). Both converge on the same dds-proxy instance, which performs a final authentication check before serving data.

```mermaid
flowchart TB
    classDef client fill:#f0f0f0,stroke:#333
    classDef ingress fill:#e8f4fd,stroke:#2196F3
    classDef authsvc fill:#fff3e0,stroke:#FF9800
    classDef appsvc fill:#e8f5e9,stroke:#4CAF50
    classDef external fill:#fce4ec,stroke:#E91E63
    classDef decision fill:#f3e5f5,stroke:#9C27B0

    NSI(["NSI Client\n(with client certificate)"]):::client
    Browser(["Browser User"]):::client
    SRAM["SRAM IdP\n(OIDC Provider)"]:::external

    subgraph mTLS_Path["mTLS Ingress — nsi-dds-proxy"]
        direction TB
        mNginx["nginx ingress controller\n\nauth-tls-verify-client: on\nauth-tls-secret: nsi-auth CA\nauth-url: nsi-auth /validate\nauth-response-headers:\n  X-Auth-Method, X-Client-DN"]:::ingress
    end

    subgraph OIDC_Path["OIDC Ingress — ana-automation-ui"]
        direction TB
        oNginx["nginx ingress controller\n\nauth-url: oauth2-proxy /oauth2/auth\nauth-response-headers:\n  Authorization,\n  X-Auth-Request-Access-Token\nconfiguration-snippet:\n  proxy_set_header X-Auth-Method \"\""]:::ingress
        Portal["ana-automation-ui\n(portal landing page)"]:::appsvc
        BackendIngress["Backend ingresses\n/dds-proxy/* /aggregator-proxy/* ..."]:::ingress
        oNginx --> Portal
        Portal --> BackendIngress
    end

    subgraph Auth_Services["Auth Services"]
        nsiAuth["nsi-auth\n\nValidates client DN\nagainst allowed list"]:::authsvc
        OAuth2["oauth2-proxy\n\nManages OIDC session\npass_access_token = true\nset_xauthrequest = true"]:::authsvc
    end

    NSI -->|"TLS handshake\n+ client certificate"| mNginx
    Browser -->|"HTTPS\n+ session cookie"| oNginx

    mNginx -.->|"auth subrequest\n(DN from ssl-client-subject-dn)"| nsiAuth
    nsiAuth -.->|"200 OK\nX-Auth-Method: mTLS\nX-Client-DN: CN=..."| mNginx

    oNginx -.->|"auth subrequest"| OAuth2
    OAuth2 -.->|"200 OK\nAuthorization: Bearer JWT\nX-Auth-Request-Access-Token: ..."| oNginx
    OAuth2 <-.->|"OIDC login\n+ token refresh"| SRAM

    subgraph DDS_Proxy["dds-proxy (AUTH_ENABLED=true)"]
        direction TB
        AuthCheck{"get_authenticated_user"}:::decision
        OIDC_Check["OIDC path\n\nJWT in Authorization or\nX-Auth-Request-Access-Token?\n→ Validate signature, issuer,\n   audience, expiry\n→ Check group membership\n   via userinfo endpoint"]:::appsvc
        mTLS_Check["mTLS path\n\nX-Auth-Method header present?\n→ Log X-Client-DN for audit"]:::appsvc
        OK(["200 — Serve data"]):::appsvc
        Reject(["401 — Unauthorized"]):::client

        AuthCheck -->|"JWT present"| OIDC_Check
        AuthCheck -->|"No JWT"| mTLS_Check
        OIDC_Check -->|"Valid"| OK
        OIDC_Check -->|"Invalid JWT"| Reject
        mTLS_Check -->|"Header set"| OK
        mTLS_Check -->|"No header"| Reject
    end

    mNginx -->|"X-Auth-Method: mTLS\nX-Client-DN: CN=..."| AuthCheck
    BackendIngress -->|"Authorization: Bearer JWT\nX-Auth-Request-Access-Token: ...\n(X-Auth-Method stripped)"| AuthCheck
```

#### Defense-in-depth measures

| Measure | Purpose |
|---|---|
| **mTLS ingress verifies client cert** against CA chain before reaching nsi-auth | Only certificates signed by a trusted CA are accepted |
| **nsi-auth validates DN** against an allowed list | Even with a valid cert, only pre-approved clients are authorized |
| **OIDC ingress strips `X-Auth-Method`** header via `configuration-snippet` | Prevents browser users from spoofing mTLS authentication by injecting the header |
| **Invalid JWT blocks request** even when `X-Auth-Method` is present | A bad JWT is always rejected — mTLS cannot rescue a failed OIDC attempt |
| **dds-proxy requires at least one method** when `AUTH_ENABLED=true` | No unauthenticated passthrough — every request must prove its identity |
| **Group-based authorization** via OIDC userinfo endpoint | OIDC users can be restricted to specific SRAM groups |

#### Header flow summary

| Header | Set by | Forwarded by | Consumed by |
|---|---|---|---|
| `X-Auth-Method: mTLS` | nsi-auth (on 200) | mTLS nginx (`auth-response-headers`) | dds-proxy (mTLS auth check) |
| `X-Client-DN` | nsi-auth (on 200) | mTLS nginx (`auth-response-headers`) | dds-proxy (audit logging) |
| `Authorization: Bearer <JWT>` | oauth2-proxy | OIDC nginx (`auth-response-headers`) | dds-proxy (OIDC auth check) |
| `X-Auth-Request-Access-Token` | oauth2-proxy | OIDC nginx (`auth-response-headers`) | dds-proxy (JWT fallback + userinfo lookup) |

#### Configuration

| Variable | Default | Description |
|---|---|---|
| `AUTH_ENABLED` | `false` | Enable authentication on all data endpoints. When `true`, every request must be authenticated via OIDC (JWT) or mTLS (header from auth service). `/health` is always unauthenticated. Replaces the former `OIDC_ENABLED`. |
| `MTLS_HEADER` | _(empty)_ | Header name that nsi-auth sets on successful validation (e.g. `X-Auth-Method`). When set and auth is enabled, the presence of this header counts as mTLS authentication. nsi-auth also sets `X-Client-DN` with the client certificate DN, which is logged for audit purposes. |
| `OIDC_ISSUER` | _(empty)_ | Expected `iss` claim in the JWT (e.g. `https://connect.test.surfconext.nl`). OIDC validation is active when this is set and auth is enabled. |
| `OIDC_AUDIENCE` | _(empty)_ | Expected `aud` claim in the JWT (e.g. `demo.pilot1.sram.surf.nl`). |
| `OIDC_JWKS_URI` | _(empty)_ | JWKS endpoint URL. Auto-discovered from `{OIDC_ISSUER}/.well-known/openid-configuration` if empty. |
| `OIDC_USERINFO_URI` | _(empty)_ | Userinfo endpoint URL. Auto-discovered from the OIDC configuration if empty. |
| `OIDC_GROUP_CLAIM` | `eduperson_entitlement` | Claim name in the userinfo response that contains group memberships. |
| `OIDC_REQUIRED_GROUPS` | `[]` | Groups required for access. Supports comma-separated (`g1,g2`) or JSON array (`["g1","g2"]`). Use `[]` for no group check (any authenticated user is allowed). **Note:** pydantic-settings JSON-parses `list` env vars, so an empty string will cause a startup error — always use `[]` instead. |
| `OIDC_JWKS_CACHE_LIFESPAN` | `300` | JWKS key cache TTL in seconds. |
| `OIDC_USERINFO_CACHE_TTL` | `60` | Userinfo response cache TTL in seconds. |

**Authentication flow** when `AUTH_ENABLED=true`:

1. **OIDC path** (if `OIDC_ISSUER` is set): Check for a JWT in the `Authorization: Bearer` header, falling back to `X-Auth-Request-Access-Token` (set by oauth2-proxy). If a token is present, validate it for signature, issuer, audience, and expiry. The `X-Auth-Request-Access-Token` fallback is needed because the nginx ingress controller has a [known issue](https://github.com/kubernetes/ingress-nginx/issues/13163) where it clears the `Authorization` header from auth subrequest responses. If a token is present but invalid, the request is rejected (mTLS does not override a bad JWT).
2. **mTLS path** (if `MTLS_HEADER` is set): Check for the configured header (e.g. `X-Auth-Method`). This header is set by the mTLS auth subrequest service (nsi-auth) and forwarded by nginx via `auth-response-headers`. The client certificate DN from `X-Client-DN` is logged for audit.
3. **Neither**: If no valid credentials are found, the request is rejected with 401.

**Access token for group authorization:** When `OIDC_REQUIRED_GROUPS` is set, the proxy needs an access token (via `X-Auth-Request-Access-Token`) to call the OIDC userinfo endpoint for group membership. This header is set by oauth2-proxy when `set_xauthrequest = true` and `pass_access_token = true`. If a valid JWT is present but the access token header is missing, the request is rejected with 401.

#### Error responses

When authentication is enabled, data endpoints may return these error responses:

| Status | Detail | Cause |
|---|---|---|
| `401` | `Token expired` | JWT `exp` claim is in the past |
| `401` | `Invalid audience` | JWT `aud` claim does not match `OIDC_AUDIENCE` |
| `401` | `Invalid issuer` | JWT `iss` claim does not match `OIDC_ISSUER` |
| `401` | `Invalid token: <reason>` | Other JWT validation failures (missing required claims, bad signature, etc.) |
| `401` | `Token validation failed` | JWKS key retrieval failed (endpoint unreachable, key not found) |
| `401` | `Missing access token for group lookup` | Group authorization required but `X-Auth-Request-Access-Token` header missing |
| `401` | `Authentication required` | No valid credentials found (no JWT, no mTLS header) |
| `403` | `Insufficient group membership` | User not in any of the required groups |
| `502` | `Failed to fetch user information` | Userinfo endpoint unreachable or returned an error |

**Defense-in-depth:** The OIDC ingress should strip the `X-Auth-Method` header to prevent clients from spoofing mTLS authentication. With nginx, use `configuration-snippet: proxy_set_header X-Auth-Method "";`. With Traefik, use a Headers middleware with `customRequestHeaders: { X-Auth-Method: "" }`.

A ready-to-use template is provided in `dds_proxy.env`. The application automatically reads this file from the working directory when it starts, so in most cases you only need to edit it in place.

If you want to maintain multiple configurations (e.g. for different environments), copy it and pass the copy explicitly via `docker run --env-file` or by exporting the variables in your shell:

```bash
cp dds_proxy.env production.env
# edit production.env

# Use with Docker:
docker run --env-file production.env ...

# Use in your shell (exports all non-comment lines as environment variables):
export $(grep -v '^#' production.env | xargs)
dds-proxy
```

Note that `docker run --env-file` expects plain `KEY=VALUE` lines — no `export` keyword, no quotes around values. The provided `dds_proxy.env` is already in this format.

## Running the Application

### From source with uv

Install dependencies and start the server:

```bash
uv sync
dds-proxy
```

The `dds-proxy` entry point starts a Uvicorn server using the host and port from your configuration. Make sure `dds_proxy.env` is present in the directory you run the command from, or export the required environment variables beforehand.

### With Python directly

If you have the package installed in your Python environment:

```bash
pip install .
dds-proxy
```

Or invoke Uvicorn manually, which lets you override host, port, and the number of workers:

```bash
uvicorn dds_proxy.main:app --host 0.0.0.0 --port 8000 --workers 4
```

Note that when using `uvicorn` directly, `DDS_PROXY_HOST` and `DDS_PROXY_PORT` are ignored — pass them as CLI arguments instead.

### With Docker

A pre-built image is available on the GitHub Container Registry:

```
ghcr.io/workfloworchestrator/nsi-dds-proxy:0.1.0
```

Run it directly, mounting your certificate files and passing configuration via environment variables:

```bash
docker run --rm \
  -p 8000:8000 \
  -v /path/to/your/certs:/certs:ro \
  -e DDS_CLIENT_CERT=/certs/client-certificate.pem \
  -e DDS_CLIENT_KEY=/certs/client-private-key.pem \
  -e DDS_CA_BUNDLE=/certs/ca-bundle.pem \
  -e DDS_BASE_URL=https://your-dds-server/dds \
  ghcr.io/workfloworchestrator/nsi-dds-proxy:0.1.0
```

Or pass all settings via an env file:

```bash
docker run --rm \
  -p 8000:8000 \
  -v /path/to/your/certs:/certs:ro \
  --env-file production.env \
  ghcr.io/workfloworchestrator/nsi-dds-proxy:0.1.0
```

If you prefer to build the image yourself:

```bash
docker build -t nsi-dds-proxy .
```

### On Kubernetes

Store your client certificate and key in a Secret, then reference them in a Deployment:

```bash
kubectl create secret generic dds-proxy-certs \
  --from-file=client-certificate.pem=/path/to/client-certificate.pem \
  --from-file=client-private-key.pem=/path/to/client-private-key.pem \
  --from-file=ca-bundle.pem=/path/to/ca-bundle.pem
```

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: nsi-dds-proxy
spec:
  replicas: 1
  selector:
    matchLabels:
      app: nsi-dds-proxy
  template:
    metadata:
      labels:
        app: nsi-dds-proxy
    spec:
      containers:
        - name: nsi-dds-proxy
          image: ghcr.io/workfloworchestrator/nsi-dds-proxy:0.1.0
          ports:
            - containerPort: 8000
          env:
            - name: DDS_BASE_URL
              value: "https://your-dds-server/dds"
            - name: DDS_PROXY_HOST
              value: "0.0.0.0"
            - name: DDS_CLIENT_CERT
              value: "/certs/client-certificate.pem"
            - name: DDS_CLIENT_KEY
              value: "/certs/client-private-key.pem"
            - name: DDS_CA_BUNDLE
              value: "/certs/ca-bundle.pem"
          volumeMounts:
            - name: certs
              mountPath: /certs
              readOnly: true
      volumes:
        - name: certs
          secret:
            secretName: dds-proxy-certs
---
apiVersion: v1
kind: Service
metadata:
  name: nsi-dds-proxy
spec:
  selector:
    app: nsi-dds-proxy
  ports:
    - port: 80
      targetPort: 8000
```

### With Helm chart

Using the same secret as above, and the `values.yaml` as below, add an `ingress` if needed,
and install with:

```shell
helm upgrade --install --namespace development --values values.yaml nsi-dds-proxy chart
```

The chart also exposes an `envFromSecret` value that binds individual environment variables to keys of an existing Kubernetes Secret (entries with an empty `secretName` are skipped, so the list can be safely templated per environment).

```yaml
image:
  pullPolicy: IfNotPresent
  repository: ghcr.io/workfloworchestrator/nsi-dds-proxy
  tag: latest
env:
  CACHE_TTL_SECONDS: '60'
  DDS_BASE_URL: https://dds.your.domain/dds
  DDS_CA_BUNDLE: /certs/ca-bundle.pem
  DDS_CLIENT_CERT: /certs/client-certificate.pem
  DDS_CLIENT_KEY: /certs/client-private-key.pem
  DDS_PROXY_HOST: 0.0.0.0
  DDS_PROXY_PORT: '8000'
  HTTP_TIMEOUT_SECONDS: '30.0'
  LOG_LEVEL: INFO
livenessProbe:
  httpGet:
    path: /health
    port: 8000
readinessProbe:
  httpGet:
    path: /health
    port: 8000
resources:
  limits:
    cpu: 1000m
    memory: 128Mi
  requests:
    cpu: 10m
    memory: 64Mi
volumeMounts:
  - mountPath: /certs
    name: certs
    readOnly: true
volumes:
  - name: certs
    secret:
      optional: false
      secretName: dds-proxy-certs
```

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
    "labelGroup": "2100-2400,3100-3400",
    "switchingServiceId": "urn:ogf:network:example.domain.toplevel:2020:topology:switch:EVTS.ANA"
  },
  ...
]
```

### GET /service-demarcation-points

Get a list of SDPs. Each SDP consists of a pair of matching STP attached to any
switching service found in all topologies.

```json
[
  {
    "stpAId": "urn:ogf:network:example.domain.toplevel:2020:topology:ps1",
    "stpZId": "urn:ogf:network:another.domain.toplevel:1999:topology:data-center-3"
  },
  ...
]
```
