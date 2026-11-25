"""Make a VAPID key pair for Web Push (T1-41), once per server:

    python -m app.push.keys

prints VAPID_PUBLIC_KEY (for the API) and VAPID_PRIVATE_KEY (for the notifier only), both base64url.
Put them in infra/.env.staging and the password manager, never in git. Keep them: with a new pair,
every browser has to turn notifications on again.
"""

import base64

from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def new_pair() -> tuple[str, str]:
    """(public, private): the public key as the browser's applicationServerKey wants it (an
    uncompressed P-256 point), the private key as its 32-byte number."""
    key = ec.generate_private_key(ec.SECP256R1())
    public = key.public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
    private = key.private_numbers().private_value.to_bytes(32, "big")
    return _b64url(public), _b64url(private)


def main() -> None:
    public, private = new_pair()
    print(f"VAPID_PUBLIC_KEY={public}")
    print(f"VAPID_PRIVATE_KEY={private}")


if __name__ == "__main__":
    main()
