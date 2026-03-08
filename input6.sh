cat > scripts/load_posthuman_endpoints.py <<'PY'
#!/usr/bin/env python3
from __future__ import annotations

import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from sqlalchemy import select

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.core.db import SessionLocal
from app.models import Network, NetworkEndpoint, Validator

SOURCE_FILE = Path("config/posthuman_endpoints.txt")

CHAIN_RE = re.compile(r"^\s*chain_id:\s*(.+?)\s*$")
VALOPER_RE = re.compile(r"^\s*valoper_address:\s*(.+?)\s*$")
URL_RE = re.compile(r"^\s*-\s*url:\s*(.+?)\s*$")


def clean_value(s: str) -> str:
    return s.strip().strip('"').strip("'")


def normalize_chain_id(chain_id: str | None) -> str | None:
    if not chain_id:
        return None
    return " ".join(chain_id.strip().split())


def normalize_valoper(valoper: str | None) -> str | None:
    if not valoper:
        return None
    v = valoper.strip()
    if not v:
        return None
    v = v.replace("@valoper", "valoper")
    v = " ".join(v.split())
    return v or None


def parse_source(text: str) -> list[dict]:
    items = []
    current = None

    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if not line.strip():
            continue

        m = CHAIN_RE.match(line)
        if m:
            if current:
                items.append(current)
            current = {
                "chain_id": normalize_chain_id(clean_value(m.group(1))),
                "valoper_address": None,
                "urls": [],
            }
            continue

        m = VALOPER_RE.match(line)
        if m and current:
            current["valoper_address"] = normalize_valoper(clean_value(m.group(1)))
            continue

        m = URL_RE.match(line)
        if m and current:
            current["urls"].append(clean_value(m.group(1)))
            continue

    if current:
        items.append(current)

    return items


def is_ip_host(host: str | None) -> bool:
    if not host:
        return False
    return bool(re.fullmatch(r"\d{1,3}(?:\.\d{1,3}){3}", host))


def classify_url(url: str) -> str:
    if url.startswith("tcp://127.0.0.1"):
        return "validator"

    try:
        p = urlparse(url)
        host = p.hostname
    except Exception:
        return "public"

    if host in {"127.0.0.1", "localhost"}:
        return "validator"

    if is_ip_host(host):
        return "validator"

    return "public"


def dedup_keep_order(items: list[str]) -> list[str]:
    seen = set()
    result = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def get_first_network_by_chain_id(db, chain_id: str):
    rows = db.execute(
        select(Network)
        .where(Network.chain_id == chain_id)
        .order_by(Network.id.asc())
    ).scalars().all()

    if len(rows) > 1:
        print(f"[WARN] duplicate networks for chain_id={chain_id}: {[r.id for r in rows]}")
    return rows[0] if rows else None


def get_first_validator(db, network_id: int, operator_address: str):
    rows = db.execute(
        select(Validator)
        .where(Validator.network_id == network_id)
        .where(Validator.operator_address == operator_address)
        .order_by(Validator.id.asc())
    ).scalars().all()

    if len(rows) > 1:
        print(
            f"[WARN] duplicate validators for network_id={network_id}, "
            f"operator_address={operator_address}: {[r.id for r in rows]}"
        )
    return rows[0] if rows else None


def get_first_endpoint(db, network_id: int, url: str):
    rows = db.execute(
        select(NetworkEndpoint)
        .where(NetworkEndpoint.network_id == network_id)
        .where(NetworkEndpoint.url == url)
        .order_by(NetworkEndpoint.id.asc())
    ).scalars().all()

    if len(rows) > 1:
        print(
            f"[WARN] duplicate endpoints for network_id={network_id}, "
            f"url={url}: {[r.id for r in rows]}"
        )
    return rows[0] if rows else None


def main() -> None:
    if not SOURCE_FILE.exists():
        raise FileNotFoundError(f"Source file not found: {SOURCE_FILE}")

    raw = SOURCE_FILE.read_text(encoding="utf-8")
    items = parse_source(raw)
    now = datetime.now(timezone.utc)

    db = SessionLocal()
    try:
        created_validators = 0
        updated_validators = 0
        added_validator_eps = 0
        updated_validator_eps = 0

        skipped_missing_valoper = []
        skipped_networks = []
        skipped_no_validator_urls = []

        for item in items:
            chain_id = item["chain_id"]
            valoper = item["valoper_address"]
            urls = dedup_keep_order(item["urls"])

            if not chain_id:
                print("[WARN] skipped block with empty chain_id")
                continue

            if not valoper:
                skipped_missing_valoper.append(
                    {
                        "chain_id": chain_id,
                        "raw_valoper": item["valoper_address"],
                        "urls_count": len(urls),
                    }
                )
                continue

            network = get_first_network_by_chain_id(db, chain_id)
            if network is None:
                skipped_networks.append((chain_id, valoper))
                continue

            validator_urls = dedup_keep_order([u for u in urls if classify_url(u) == "validator"])
            if not validator_urls:
                skipped_no_validator_urls.append((chain_id, valoper))
                continue

            validator = get_first_validator(db, network.id, valoper)

            if validator is None:
                validator = Validator(
                    network_id=network.id,
                    moniker="PostHuman",
                    operator_address=valoper,
                    consensus_address=None,
                    is_main=1,
                    is_enabled=1,
                    created_at=now,
                    updated_at=now,
                )
                db.add(validator)
                db.flush()
                created_validators += 1
            else:
                validator.moniker = validator.moniker or "PostHuman"
                validator.is_main = 1
                validator.is_enabled = 1
                validator.updated_at = now
                db.flush()
                updated_validators += 1

            for idx, url in enumerate(validator_urls, start=1):
                exists = get_first_endpoint(db, network.id, url)

                if exists is None:
                    db.add(
                        NetworkEndpoint(
                            network_id=network.id,
                            endpoint_type="rpc",
                            label=f"validator_rpc{idx}",
                            url=url,
                            priority=idx,
                            is_public=0,
                            is_enabled=1,
                            created_at=now,
                            updated_at=now,
                        )
                    )
                    added_validator_eps += 1
                else:
                    exists.endpoint_type = "rpc"
                    exists.label = f"validator_rpc{idx}"
                    exists.priority = idx
                    exists.is_public = 0
                    exists.is_enabled = 1
                    exists.updated_at = now
                    updated_validator_eps += 1

        db.commit()

        print(f"Created validators: {created_validators}")
        print(f"Updated validators: {updated_validators}")
        print(f"Added validator RPC endpoints: {added_validator_eps}")
        print(f"Updated validator RPC endpoints: {updated_validator_eps}")

        if skipped_missing_valoper:
            print("\nSkipped blocks with missing/broken valoper_address:")
            for row in skipped_missing_valoper:
                print(
                    f"  - chain_id={row['chain_id']} | "
                    f"raw_valoper={row['raw_valoper']} | urls={row['urls_count']}"
                )

        if skipped_networks:
            print("\nSkipped networks not found in DB by chain_id:")
            for chain_id, valoper in skipped_networks:
                print(f"  - {chain_id} | {valoper}")

        if skipped_no_validator_urls:
            print("\nSkipped blocks with no validator RPC URLs:")
            for chain_id, valoper in skipped_no_validator_urls:
                print(f"  - {chain_id} | {valoper}")

    finally:
        db.close()


if __name__ == "__main__":
    main()
PY
