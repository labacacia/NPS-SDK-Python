-- NIP CA Server — SQLite schema
-- NPS-3 §8 | Copyright 2026 INNO LOTUS PTY LTD | Apache-2.0

CREATE TABLE IF NOT EXISTS nip_certificates (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    nid            TEXT    NOT NULL,
    entity_type    TEXT    NOT NULL CHECK (entity_type IN ('agent','node','operator')),
    serial         TEXT    NOT NULL UNIQUE,
    pub_key        TEXT    NOT NULL,
    capabilities   TEXT    NOT NULL DEFAULT '[]',
    scope_json     TEXT    NOT NULL DEFAULT '{}',
    issued_by      TEXT    NOT NULL,
    issued_at      TEXT    NOT NULL,
    expires_at     TEXT    NOT NULL,
    revoked_at     TEXT,
    revoke_reason  TEXT,
    metadata_json  TEXT,
    created_at     TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE INDEX IF NOT EXISTS idx_nip_certs_nid    ON nip_certificates (nid, issued_at DESC);
CREATE INDEX IF NOT EXISTS idx_nip_certs_serial ON nip_certificates (serial);
CREATE INDEX IF NOT EXISTS idx_nip_certs_revoked ON nip_certificates (revoked_at)
    WHERE revoked_at IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_nip_certs_expires ON nip_certificates (expires_at);
