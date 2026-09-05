# ADR 0004 — Opaque cookie sessions
Status: Accepted with implementation details requiring review

Passwords use pwdlib Argon2. Browser receives forge_session, HttpOnly, SameSite=Lax, Path=/; Secure is mandatory in production and disabled only for local HTTP. Database stores only SHA-256 token hashes. Sessions expire after eight hours, are revoked on logout, and user/organization activity is checked on every request. No browser token storage. Unsafe HTTP requests require an exact configured Origin (including login); requests without Origin fail closed. Login is rate limited using Redis, failing closed when unavailable.

Login requires Idempotency-Key. A keyed HMAC derives an opaque session token from the actor, random client key and credential request fingerprint, allowing exact retry without saving plaintext tokens or passwords. Fingerprints are keyed to resist offline password guessing. Expired or revoked login replays fail and require a new key; they do not resurrect sessions. Logout is naturally idempotent and clears the cookie even after revocation. Review before production: expiry, rate limits, account recovery and secret rotation/logout-all policy.
