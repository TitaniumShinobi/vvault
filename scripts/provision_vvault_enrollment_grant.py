#!/usr/bin/env python3
"""Provision one VVAULT enrollment grant outside the web runtime.

The raw invitation is printed once. PostgreSQL receives only its keyed digest.
"""

from __future__ import annotations

import argparse
import os
import secrets
from datetime import datetime, timedelta, timezone

from vvault.server.vvault_auth_repository import VVaultAuthRepository
from vvault.server.vvault_enrollment import keyed_digest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--email", required=True)
    parser.add_argument(
        "--grant-type", choices=("invitation", "allowlist", "owner_bootstrap"),
        default="invitation",
    )
    parser.add_argument("--ttl-hours", type=int, default=24)
    parser.add_argument(
        "--target-user-id",
        help="Existing LEGACY_PENDING user UUID to claim without changing its ownership UUID",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not 1 <= args.ttl_hours <= 168:
        raise SystemExit("--ttl-hours must be between 1 and 168")
    hmac_key = str(os.environ.get("VVAULT_ENROLLMENT_HMAC_KEY") or "")
    if len(hmac_key) < 32:
        raise SystemExit("VVAULT_ENROLLMENT_HMAC_KEY must contain at least 32 characters")
    raw_token = None if args.grant_type == "allowlist" else secrets.token_urlsafe(32)
    VVaultAuthRepository().provision_admission_grant(
        grant_type=args.grant_type,
        email=args.email,
        token_digest=keyed_digest(raw_token, hmac_key) if raw_token else None,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=args.ttl_hours),
        target_user_id=args.target_user_id,
    )
    if raw_token:
        print(raw_token)
    else:
        print("allowlist grant provisioned")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
