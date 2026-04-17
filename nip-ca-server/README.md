# NIP CA Server — Python

FastAPI + SQLite implementation of the NIP Certificate Authority (NPS-3 §8).

## Quick Start

```bash
cp .env.example .env   # fill in required vars
docker compose up -d
```

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `NIP_CA_NID` | Yes | — | CA NID, e.g. `urn:nps:org:ca.example.com` |
| `NIP_CA_PASSPHRASE` | Yes | — | Key file encryption passphrase |
| `NIP_CA_BASE_URL` | Yes | — | Public base URL, e.g. `https://ca.example.com` |
| `NIP_CA_DISPLAY_NAME` | No | `NPS CA` | Human-readable CA name |
| `NIP_CA_KEY_FILE` | No | `/data/ca.key.enc` | Encrypted CA key file path |
| `NIP_CA_DB_PATH` | No | `/data/ca.db` | SQLite database path |
| `NIP_CA_AGENT_VALIDITY_DAYS` | No | `30` | Agent certificate validity |
| `NIP_CA_NODE_VALIDITY_DAYS` | No | `90` | Node certificate validity |
| `NIP_CA_RENEWAL_WINDOW_DAYS` | No | `7` | Days before expiry that renewal opens |
| `PORT` | No | `17440` | HTTP port |

## API

| Method | Path | Description |
|--------|------|-------------|
| POST | `/v1/agents/register` | Register Agent, issue IdentFrame |
| POST | `/v1/agents/{nid}/renew` | Renew certificate |
| POST | `/v1/agents/{nid}/revoke` | Revoke certificate |
| GET | `/v1/agents/{nid}/verify` | Verify / OCSP check |
| POST | `/v1/nodes/register` | Register Node, issue IdentFrame |
| GET | `/v1/ca/cert` | CA public key |
| GET | `/v1/crl` | Certificate Revocation List |
| GET | `/.well-known/nps-ca` | CA discovery document |
| GET | `/health` | Health check |

## Local Development

```bash
pip install -r requirements.txt
NIP_CA_NID=urn:nps:org:ca.local \
  NIP_CA_PASSPHRASE=dev-pass \
  NIP_CA_BASE_URL=http://localhost:17440 \
  uvicorn main:app --reload --port 17440
```

## Stack

- **Runtime**: Python 3.12
- **Framework**: FastAPI + Uvicorn
- **Crypto**: `cryptography` (Ed25519 + AES-256-GCM + PBKDF2)
- **Storage**: SQLite
