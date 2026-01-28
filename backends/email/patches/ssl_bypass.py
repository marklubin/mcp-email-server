"""
SSL Bypass Patch for ProtonMail Bridge

ProtonMail Bridge uses a self-signed certificate for local IMAP/SMTP connections.
This monkey patch disables SSL certificate verification for the email backend.

SECURITY NOTE: This patch only affects connections to localhost (ProtonMail Bridge).
It should be applied before importing the email server module.

Usage:
    from backends.email.patches.ssl_bypass import apply_patch
    apply_patch()

    # Now import the email server
    from mcp_email_server import mcp
"""

import ssl

_original_create_default_context = ssl.create_default_context
_patch_applied = False


def _patched_create_default_context(*args, **kwargs):
    """Create an SSL context that doesn't verify certificates."""
    ctx = _original_create_default_context(*args, **kwargs)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def apply_patch():
    """
    Apply the SSL bypass patch.

    This is idempotent - calling multiple times has no additional effect.
    """
    global _patch_applied

    if _patch_applied:
        return

    ssl.create_default_context = _patched_create_default_context
    _patch_applied = True


def is_patched() -> bool:
    """Check if the patch has been applied."""
    return _patch_applied
