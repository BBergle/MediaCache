"""Versioned URLs for files under web/static.

Browsers keep /static files across updates, so a new template could run against
last release's app.js or custom.css. ``static_url()`` appends a hash of the
file's contents, so the URL changes exactly when the file does, on Docker, git
checkouts and dev builds alike. Registered as a template global in web.config.

Kept free of app imports so tests can load it directly (several test modules
replace web.config with a MagicMock).
"""

import hashlib
from pathlib import Path

STATIC_DIR = Path(__file__).parent / "static"

# path -> (mtime_ns, digest); recomputed only when the file changes on disk
_static_hashes: dict = {}


def static_url(path: str) -> str:
    """Return ``/static/<path>?v=<content hash>`` for a file under web/static."""
    file_path = STATIC_DIR / path
    try:
        mtime = file_path.stat().st_mtime_ns
    except OSError:
        return f"/static/{path}"
    cached = _static_hashes.get(path)
    if cached is None or cached[0] != mtime:
        digest = hashlib.sha1(file_path.read_bytes()).hexdigest()[:10]
        cached = (mtime, digest)
        _static_hashes[path] = cached
    return f"/static/{path}?v={cached[1]}"
