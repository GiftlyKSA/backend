# API input handling and rate-limit security review

Reviewed on 2026-10-01. This is a targeted source review and regression check, not a
penetration test, production verification, or a replacement for the broader backend
review. No Docker or live database changes were performed.

Later update on 2026-10-01: admin dashboard and `/api/admin/` routes now have a separate
100-request/60-second bucket (`RATE_LIMIT_ADMIN_MAX_REQUESTS=100`,
`RATE_LIMIT_ADMIN_WINDOW_SECONDS=60`). Cookie-only requests are counted per IP;
verified bearer identities are counted per user. Path classification only selects
a throttle policy; it never grants admin access. The stricter admin login throttle
of five attempts per five minutes is unchanged. Three boundary/isolation tests verify
the dashboard root, dashboard subroutes, and admin API paths.

## Implemented changes

- HTTP middleware selects a Redis-backed limit of 60 requests per 60 seconds for a
  cryptographically verified bearer-token identity, or 30 requests per 3,600 seconds
  for an unauthenticated IP. Invalid bearer tokens use the anonymous policy.
- Both policies have separate environment settings and reject zero/negative settings.
  Existing deployment overrides must be updated; changing code defaults does not
  override environment values. Existing Redis counters expire normally, so a rollout
  can temporarily retain the old counter's expiry. No destructive counter reset is used.
- Withdrawal and withdrawal-rejection schemas reject undeclared properties rather
  than silently ignoring them. Valid request bodies and responses remain unchanged.
- Added regression checks for both limit boundaries, invalid tokens, exemptions,
  request-model extra-field restrictions, literal-text serialization, and Jinja escaping.

## Plain-text contract and UI requirements

Free-form text remains literal text. For example, `<script>alert(1)</script>` may be
stored as a message and returned with those same characters in an application/json
response. The chat service encrypts the content at rest; retrieval decrypts it without
executing it. Field-specific validation still applies to email, URLs, enums, lengths,
money, identifiers, and structured data.

The admin escapes text at HTML rendering time, including quoted attribute values.
It does not mark user content safe, evaluate it, or insert it using raw HTML DOM APIs
in the inspected templates/scripts. JSON responses also receive `nosniff` headers.
Precise delivery location inputs have since been removed from the API and admin.

HTML-encoding every stored/API value would alter the public contract and create
double-escaping problems. Sanitization therefore happens at the display boundary,
through context-appropriate escaping, rather than deleting code-like characters.
The mobile/web UI must use text widgets, escaped bindings, or DOM `textContent`.
Never evaluate returned strings, insert them into raw HTML, compile them as templates,
or HTML-decode them into markup. If rich HTML is introduced later, it needs a separately
approved allowlist sanitizer and dedicated security tests; no rich HTML is supported here.

## OWASP Top 10:2025 control checks

These are observed controls and their verification scope, not complete category clearance.

| Category | Inspected evidence | Remaining verification |
| --- | --- | --- |
| A01 Access control | `app/core/deps.py` authenticates and checks roles; domain services enforce ownership, including chat membership and media grants. All discovered `*Request` schemas reject extra fields. | Exhaustive cross-account HTTP/WebSocket testing needs the integration environment. |
| A02 Misconfiguration | `app/core/config.py` production interlocks, restricted production CORS, admin cookie/CSRF handling, security headers/CSP in `app/main.py`. | Deployed proxy, TLS, origin access, and environment settings are unverified. |
| A03 Supply chain | `uv.lock` retained; no dependencies changed. Ruff security rules remain enabled. | No fresh dependency advisory audit or artifact/provenance audit was performed in this change. |
| A04 Cryptography | `app/core/crypto.py` uses AES-GCM with fresh nonces and record-bound AAD; JWT algorithms are pinned. | Production key custody and rotation are operational checks. |
| A05 Injection | Pydantic request schemas; SQLAlchemy expressions and bound parameters in inspected repositories, including raw promo SQL; admin HTML escaping and safe DOM text APIs. | Separate UI rendering and a complete database-backed injection test suite are unverified. |
| A06 Design | Bounded HTTP/frame/upload input, Redis atomic throttles, server-side ownership and financial primitives retained. | Production load, abuse simulations, and concurrent financial integration tests are unverified. |
| A07 Authentication | `app/core/jwt.py` validates algorithm/issuer/audience/expiry; `require_auth` additionally validates live credentials; OTP limits, refresh logic, and production admin TOTP remain intact. | The throttle's bearer identity selection does not itself grant authorization; revoked identities are rejected by endpoint authentication. Full lifecycle integration tests require Redis/PostgreSQL. |
| A08 Integrity | Simulation callbacks verify signatures; simulation routes are excluded from production; production payments remain disabled. | Dhamen integration and vendor contracts remain outside this change and are not verified. |
| A09 Logging | Correlation IDs, safe error responses, log scrubbing, and committed-action audit mechanisms remain intact. | Deployed alerting, log access, retention, and database audit integrity need operational verification. |
| A10 Exceptional conditions | Transaction rollback, safe unexpected-error envelopes, Redis timeouts, and fail-closed admin/guarded WebSocket checks remain intact. | Ordinary HTTP throttling deliberately fails open on Redis outage; external edge protection is recommended. |

## Operational limits and impact

- **Medium, 6/10, retained design limitation:** ordinary HTTP rate limiting fails open
  when Redis is unavailable (`app/core/ratelimit.py`). Impact: the request budget is
  unenforced during an outage. Endpoint authentication is still required. Use an edge
  rate limiter/WAF and alert on Redis failures; changing availability behavior needs
  an explicit operational decision.
- **Medium, 5/10, consequence of requested policy:** unauthenticated clients behind
  one NAT/proxy share 30 requests/hour. The former cookie-only dashboard allowance
  issue is resolved by the separate admin policy above. Impact: public/login requests
  can receive 429 after the anonymous budget; admin routes have their own budget.
  Health and CORS preflight remain exempt. Verify trusted-proxy deployment and tune
  policies explicitly if shared-IP/admin usage needs a different allowance.
- **Unconfirmed external risk:** the mobile repository was not inspected or changed.
  An unsafe UI HTML sink could execute otherwise literal API text. The updated UI-agent
  handoff requires safe text rendering and must be verified in that repository.

## Deployment and rollback

Set `RATE_LIMIT_MAX_REQUESTS=60`, `RATE_LIMIT_WINDOW_SECONDS=60`,
`RATE_LIMIT_ANONYMOUS_MAX_REQUESTS=30`, and
`RATE_LIMIT_ANONYMOUS_WINDOW_SECONDS=3600` in deployment configuration.
No migration or data backfill is needed. To restore the previous shared policy,
set both maxima to 120 and both windows to 60; revert the schema change only if clients
need the former unknown-field behavior. Keep input rendering protections in place.

## Sources

- [OWASP input validation](https://cheatsheetseries.owasp.org/cheatsheets/Input_Validation_Cheat_Sheet.html)
- [OWASP XSS prevention](https://cheatsheetseries.owasp.org/cheatsheets/Cross_Site_Scripting_Prevention_Cheat_Sheet.html)
- [OWASP SQL injection prevention](https://cheatsheetseries.owasp.org/cheatsheets/SQL_Injection_Prevention_Cheat_Sheet.html)
- [OWASP Top 10:2025](https://top10.owasp.org/2025/)

## Local verification

- Regression tests were run before the rate-policy implementation and failed at both
  new limits and the anonymous window. They passed after the implementation.
- Withdrawal undeclared-field tests failed before the schema fix and passed afterward.
- Focused input/security suite: 12 passed; withdrawal schema suite: 4 passed.
- Final full unit suite after the admin policy update: 470 passed, including OpenAPI drift verification. The only
  warning is an existing Starlette/httpx deprecation notice.
- Both all-files pre-commit stages passed, including Ruff lint/format, strict mypy,
  JSON/YAML/TOML validation, private-key detection, and merge-marker checks.
- OpenAPI regenerated schema differs only in withdrawal `additionalProperties: false`.
- Full `uv run --locked pytest` was attempted and interrupted after repeated missing
  local PostgreSQL/Redis skips. Integration results are not claimed. No Docker ran.
- The first unit-only collection lacked worker test environment settings; repeating
  with explicit dummy test settings passed. No production secrets were read.
