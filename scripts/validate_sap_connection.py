"""Validate the SAP S/4HANA Cloud trial connection.

Reads SAP_* env vars, does the OAuth client_credentials flow, issues one
read against the Purchase Order OData service. Reports success or the
specific failure.

Run this:
- After setting up the trial tenant and adding env vars
- Before every integration test session
- After tenant renewal

Usage:
    make sap-validate
    # or
    uv run python scripts/validate_sap_connection.py
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


# Required env vars and what they look like
REQUIRED_ENV = {
    "SAP_BASE_URL": "https://api.s4hana-cloud.cfapps.<region>.hana.ondemand.com",
    "SAP_TOKEN_URL": "https://<tenant>.authentication.<region>.hana.ondemand.com/oauth/token",
    "SAP_CLIENT_ID": "<from BTP cockpit service key>",
    "SAP_CLIENT_SECRET": "<from BTP cockpit service key>",
}


@dataclass
class ValidationResult:
    ok: bool
    message: str
    details: dict | None = None


def check_env() -> ValidationResult:
    """Verify all required env vars are set."""
    missing = [k for k in REQUIRED_ENV if not os.environ.get(k)]
    if missing:
        msg_lines = ["Missing required env vars:"]
        for k in missing:
            msg_lines.append(f"  {k}  ({REQUIRED_ENV[k]})")
        msg_lines.append("")
        msg_lines.append("Set these in .env. See docs/sap_sandbox_setup.md step 2.")
        return ValidationResult(False, "\n".join(msg_lines))
    return ValidationResult(True, "All SAP env vars present.")


def get_oauth_token() -> ValidationResult:
    """Run the client_credentials flow."""
    try:
        import httpx
    except ImportError:
        return ValidationResult(
            False,
            "httpx not installed. Run `make setup` or `uv sync` first.",
        )

    token_url = os.environ["SAP_TOKEN_URL"]
    client_id = os.environ["SAP_CLIENT_ID"]
    client_secret = os.environ["SAP_CLIENT_SECRET"]

    try:
        resp = httpx.post(
            token_url,
            data={"grant_type": "client_credentials"},
            auth=(client_id, client_secret),
            timeout=30.0,
        )
    except httpx.HTTPError as e:
        return ValidationResult(
            False,
            f"HTTP error reaching token URL: {e}\n"
            f"  Token URL: {token_url}\n"
            f"  Check SAP_TOKEN_URL is correct and network reachable.",
        )

    if resp.status_code != 200:
        return ValidationResult(
            False,
            f"OAuth token request failed: HTTP {resp.status_code}\n"
            f"  Response: {resp.text[:500]}\n"
            f"  Check SAP_CLIENT_ID / SAP_CLIENT_SECRET match the service key in BTP cockpit.",
        )

    body = resp.json()
    if "access_token" not in body:
        return ValidationResult(
            False,
            f"OAuth response missing access_token. Body keys: {list(body.keys())}",
        )

    return ValidationResult(
        True,
        f"OAuth token acquired (expires_in={body.get('expires_in', '?')}s).",
        details={"token_prefix": body["access_token"][:12] + "..."},
    )


def test_read_po(token: str) -> ValidationResult:
    """Issue one read against the Purchase Order OData service."""
    import httpx

    base_url = os.environ["SAP_BASE_URL"].rstrip("/")
    url = f"{base_url}/sap/opu/odata/sap/API_PURCHASEORDER_PROCESS_SRV/A_PurchaseOrder"

    try:
        resp = httpx.get(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
            },
            params={"$top": 1, "$format": "json"},
            timeout=30.0,
        )
    except httpx.HTTPError as e:
        return ValidationResult(
            False,
            f"HTTP error reading PO service: {e}\n  URL: {url}",
        )

    if resp.status_code == 401:
        return ValidationResult(
            False,
            "401 Unauthorized — token did not authorize against the PO service.\n"
            "  Likely cause: service key is missing the API_PURCHASEORDER scope.\n"
            "  Fix: regenerate the service key in BTP cockpit with all scopes enabled.",
        )

    if resp.status_code == 404:
        return ValidationResult(
            False,
            "404 Not Found — base URL may be wrong, or the PO service is not exposed in this tenant.\n"
            f"  URL: {url}\n"
            "  Check SAP_BASE_URL matches the api_url from your service key.",
        )

    if resp.status_code != 200:
        return ValidationResult(
            False,
            f"PO read failed: HTTP {resp.status_code}\n"
            f"  Response: {resp.text[:500]}",
        )

    body = resp.json()
    results = body.get("d", {}).get("results", []) if isinstance(body, dict) else []
    schema_fields = list(results[0].keys()) if results else []
    return ValidationResult(
        True,
        f"PO read OK. Got {len(results)} record(s); first record has {len(schema_fields)} fields.",
        details={"sample_fields": schema_fields[:10]},
    )


def main() -> None:
    # Try to load .env if python-dotenv is installed
    try:
        from dotenv import load_dotenv
        env_path = REPO_ROOT / ".env"
        if env_path.exists():
            load_dotenv(env_path)
            print(f"Loaded env from {env_path}")
    except ImportError:
        pass

    print("=" * 60)
    print("SAP S/4HANA Cloud trial — connection validator")
    print("=" * 60)
    print()

    # Step 1: env vars
    print("Step 1 — check env vars")
    result = check_env()
    print("  ✓" if result.ok else "  ✗", result.message.split("\n")[0])
    if not result.ok:
        for line in result.message.split("\n")[1:]:
            print("    " + line)
        sys.exit(1)

    # Step 2: OAuth
    print()
    print("Step 2 — OAuth client_credentials flow")
    oauth = get_oauth_token()
    print("  ✓" if oauth.ok else "  ✗", oauth.message.split("\n")[0])
    if not oauth.ok:
        for line in oauth.message.split("\n")[1:]:
            print("    " + line)
        sys.exit(1)

    import httpx
    token_resp = httpx.post(
        os.environ["SAP_TOKEN_URL"],
        data={"grant_type": "client_credentials"},
        auth=(os.environ["SAP_CLIENT_ID"], os.environ["SAP_CLIENT_SECRET"]),
    ).json()
    token = token_resp["access_token"]

    # Step 3: PO read
    print()
    print("Step 3 — Purchase Order OData read")
    read = test_read_po(token)
    print("  ✓" if read.ok else "  ✗", read.message.split("\n")[0])
    if read.details:
        print(f"    Sample fields: {read.details.get('sample_fields')}")
    if not read.ok:
        for line in read.message.split("\n")[1:]:
            print("    " + line)
        sys.exit(1)

    print()
    print("=" * 60)
    print("All checks passed. The agent's SAP connector can read POs.")
    print("=" * 60)


if __name__ == "__main__":
    main()
