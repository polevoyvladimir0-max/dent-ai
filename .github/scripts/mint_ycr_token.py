#!/usr/bin/env python3

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request


def request_token(payload: dict[str, str]) -> str:
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        "https://iam.api.cloud.yandex.net/iam/v1/tokens",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read().decode()
            parsed = json.loads(body)
            return parsed.get("iamToken", "") or ""
    except urllib.error.HTTPError as err:
        print(f"iam_http_error:{err.code}", file=sys.stderr)
    except urllib.error.URLError:
        print("iam_url_error", file=sys.stderr)
    except Exception:
        print("iam_unknown_error", file=sys.stderr)
    return ""


def maybe_install_jwt() -> None:
    try:
        import jwt  # type: ignore  # noqa: F401
    except ImportError:
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "pyjwt[crypto]"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


def main() -> int:
    secret = os.environ.get("REGISTRY_PASSWORD", "").strip()
    username = os.environ.get("REGISTRY_USER", "").strip()

    if not secret:
        return 0

    token = ""

    if secret.startswith("{"):
        try:
            data = json.loads(secret)
            required = {"id", "service_account_id", "private_key"}
            if required.issubset(data):
                maybe_install_jwt()
                import jwt  # type: ignore

                now = int(time.time())
                payload = {
                    "aud": "https://iam.api.cloud.yandex.net/iam/v1/tokens",
                    "iss": data["service_account_id"],
                    "iat": now,
                    "exp": now + 3600,
                }
                headers = {"alg": "PS256", "typ": "JWT", "kid": data["id"]}
                signed = jwt.encode(
                    payload,
                    data["private_key"],
                    algorithm="PS256",
                    headers=headers,
                )
                minted = request_token({"jwt": signed})
                if minted:
                    token = minted
                    username = "oauth"
        except Exception:
            print("sa_token_failed", file=sys.stderr)
            token = ""

    if not token:
        minted = request_token({"yandexPassportOauthToken": secret})
        if minted:
            token = minted
            username = "oauth"

    if token:
        print(f"username={username or 'oauth'}")
        print(f"token={token}")

    return 0


if __name__ == "__main__":
    sys.exit(main())

