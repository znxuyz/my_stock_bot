"""
Discord Interaction 簽名驗證（Ed25519）。
"""
from nacl.signing import VerifyKey

import config


def verify_signature(raw_body, signature, timestamp):
    """驗證 X-Signature-Ed25519 header。失敗時回 False，不丟例外。"""
    if not config.DISCORD_PUBLIC_KEY:
        return False
    try:
        VerifyKey(bytes.fromhex(config.DISCORD_PUBLIC_KEY)).verify(
            timestamp.encode() + raw_body, bytes.fromhex(signature),
        )
        return True
    except Exception:
        return False
