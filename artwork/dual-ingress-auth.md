# Dual-Ingress Authentication Architecture

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

## Defense-in-Depth Measures

| Measure | Purpose |
|---|---|
| **mTLS ingress verifies client cert** against CA chain before reaching nsi-auth | Only certificates signed by a trusted CA are accepted |
| **nsi-auth validates DN** against an allowed list | Even with a valid cert, only pre-approved clients are authorized |
| **OIDC ingress strips `X-Auth-Method`** header via `configuration-snippet` | Prevents browser users from spoofing mTLS authentication by injecting the header |
| **Invalid JWT blocks request** even when `X-Auth-Method` is present | A bad JWT is always rejected — mTLS cannot rescue a failed OIDC attempt |
| **dds-proxy requires at least one method** when `AUTH_ENABLED=true` | No unauthenticated passthrough — every request must prove its identity |
| **Group-based authorization** via OIDC userinfo endpoint | OIDC users can be restricted to specific SRAM groups |

## Header Flow Summary

| Header | Set by | Forwarded by | Consumed by |
|---|---|---|---|
| `X-Auth-Method: mTLS` | nsi-auth (on 200) | mTLS nginx (`auth-response-headers`) | dds-proxy (mTLS auth check) |
| `X-Client-DN` | nsi-auth (on 200) | mTLS nginx (`auth-response-headers`) | dds-proxy (audit logging) |
| `Authorization: Bearer <JWT>` | oauth2-proxy | OIDC nginx (`auth-response-headers`) | dds-proxy (OIDC auth check) |
| `X-Auth-Request-Access-Token` | oauth2-proxy | OIDC nginx (`auth-response-headers`) | dds-proxy (JWT fallback + userinfo lookup) |
