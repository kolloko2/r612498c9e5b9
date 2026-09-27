# Security

Backend SECURITY_AUDIT_DIR enables append-only HTTP/WS metadata records with fsync,
daily UTC rotation, hourly archival of completed days to gzip and no automatic
deletion. Minimum retention policy is183 days; originals are also retained.
Protect and back up this directory; file retention cannot prevent disk loss or
host-admin tampering. This is not a cryptographically signed log. Secrets/bodies,
query strings and untrusted raw paths are excluded; registered route templates
are used. Start/end IDs expose interrupted requests without retrying mutations.
Native mock mode may omit the directory and explicitly reports audit disabled.
Records are committed in groups: one writer thread appends the queued records to an
already open file and issues a single fsync per group. `append` still returns only
after the fsync that included the caller's own record, and every writer in a failed
group receives the error, so the fail-closed guarantee is unchanged while concurrent
requests no longer serialize behind one fsync each.
Admin `/audit` shows bounded daily records. Existing account/business audits remain.
Voice records HTTP/WS metadata in a separate journal, selectable in the same
admin cabinet. OS/firewall actions, media contents and unsaved UI clicks are not
covered; no claim of cryptographically tamper-proof full-system auditing is made.

LDAPS requires certificate validation and explicit local-account linking; directory
users cannot become admins. A separate synthetic directory can be enabled for
development. Certificate creation alone does not install trust or enable TLS.
The TLS overlay is now active on this workstation, and its public training CA
was added to the current user's Windows trust store with explicit consent.
SIP TLS/SRTP negotiation passed, but the test phone sent no incoming RTP;
end-to-end media remains unverified. MicroSIP server identity verification is
not asserted. See DIRECTORY_AUTH.md, TLS.md and voice/docs/VERIFICATION.md.

Synthetic data only. No secrets or real 112 data. Credentials are environment variables. Review external inference provider logging and downstream policies. Future on-prem inference is an option, not bootstrap scope.
