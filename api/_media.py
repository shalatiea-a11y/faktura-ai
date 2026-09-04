"""Best-effort media-type detection for uploaded invoice files.

Browsers don't always set a useful `file.type` for a PDF - it can come
through empty or as `application/octet-stream` (seen from some Android
share-sheet flows and files whose extension the OS doesn't recognize).
Sending that straight to Claude as an `image` content block gets rejected
by the API, which used to surface as a generic "Fel" in the UI. Falling
back to the filename extension fixes the common case; if neither the
reported type nor the filename tells us anything, the caller can reject
clearly instead of guessing.
"""
EXTENSION_MAP = {
    '.pdf': 'application/pdf',
    '.png': 'image/png',
    '.jpg': 'image/jpeg',
    '.jpeg': 'image/jpeg',
    '.webp': 'image/webp',
    '.gif': 'image/gif',
}

SUPPORTED = {'application/pdf', 'image/png', 'image/jpeg', 'image/webp', 'image/gif'}


def resolve_media_type(media_type, filename):
    if media_type in SUPPORTED:
        return media_type
    name = (filename or '').lower()
    for ext, mt in EXTENSION_MAP.items():
        if name.endswith(ext):
            return mt
    return media_type or ''
