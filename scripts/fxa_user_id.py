#!/usr/bin/env python3
import argparse
import sys

from fxa.oauth import Client
from fxa.tools.bearer import get_bearer_token

CLIENT_ID: str = "5882386c6d801776"
SCOPE: str = "profile:uid"
FXA_API_ACCOUNTS_URL: str = "https://api.accounts.firefox.com"
FXA_OAUTH_SERVER_URL: str = "https://oauth.accounts.firefox.com"

client = Client(f"{FXA_API_ACCOUNTS_URL}/v1")


def token_to_user_id(token: str) -> str:
    raw = token.strip().removeprefix("Bearer ").split()
    tok = raw[0] if raw else token.strip()
    profile = client.verify_token(tok, scope=SCOPE, include_verification_source=False)
    return profile["user"]


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Get FxA user_id from token or email+password."
    )
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--token", "-t", metavar="TOKEN", help="Bearer token")
    g.add_argument(
        "--email", "-e", metavar="EMAIL", help="FxA email (use with --password)"
    )
    ap.add_argument(
        "--password", "-p", metavar="PWD", help="FxA password (use with --email)"
    )
    args = ap.parse_args()

    if args.token:
        uid = token_to_user_id(args.token)
    else:
        if not args.password:
            ap.error("--password required when using --email")
        token = get_bearer_token(
            args.email,
            args.password,
            scopes=[SCOPE],
            client_id=CLIENT_ID,
            account_server_url=FXA_API_ACCOUNTS_URL,
            oauth_server_url=FXA_OAUTH_SERVER_URL,
        )
        uid = token_to_user_id(token)
    print(f"User ID: {uid}")


if __name__ == "__main__":
    main()
