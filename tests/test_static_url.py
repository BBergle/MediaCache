"""
Versioned static URLs (web.config.static_url).

Browsers keep /static files across updates, so after an upgrade a new template
could run against the previous release's app.js or custom.css. Every CSS/JS
reference goes through static_url(), which appends a hash of the file's
contents, so the URL changes exactly when the file does.
"""

import hashlib
import os
import re
from pathlib import Path

import pytest

import web.config as web_config
from web.config import static_url

TEMPLATES_DIR = Path(web_config.TEMPLATES_DIR)


@pytest.fixture
def static_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(web_config, "STATIC_DIR", tmp_path)
    monkeypatch.setattr(web_config, "_static_hashes", {})
    return tmp_path


def test_url_carries_content_hash(static_dir):
    (static_dir / "js").mkdir()
    (static_dir / "js" / "app.js").write_bytes(b"console.log(1);")
    expected = hashlib.sha1(b"console.log(1);").hexdigest()[:10]

    assert static_url("js/app.js") == f"/static/js/app.js?v={expected}"


def test_url_changes_when_file_changes(static_dir):
    css = static_dir / "custom.css"
    css.write_bytes(b"body { color: red; }")
    before = static_url("custom.css")

    css.write_bytes(b"body { color: blue; }")
    st = css.stat()
    os.utime(css, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))
    after = static_url("custom.css")

    assert before != after
    assert after.startswith("/static/custom.css?v=")


def test_unchanged_file_keeps_its_url(static_dir):
    (static_dir / "a.css").write_bytes(b"a {}")
    assert static_url("a.css") == static_url("a.css")


def test_missing_file_falls_back_to_plain_url(static_dir):
    assert static_url("js/missing.js") == "/static/js/missing.js"


def test_registered_as_template_global():
    assert web_config.templates.env.globals["static_url"] is static_url


def test_real_static_files_resolve():
    for path in ("js/app.js", "css/custom.css", "css/plex-theme.css", "js/vendor/htmx.min.js"):
        assert re.fullmatch(rf"/static/{re.escape(path)}\?v=[0-9a-f]{{10}}", static_url(path)), path


def test_templates_do_not_hardcode_css_or_js_paths():
    """Every CSS/JS include must go through static_url() so updates bust the browser cache."""
    offenders = []
    for template in TEMPLATES_DIR.rglob("*.html"):
        for lineno, line in enumerate(template.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r'(href|src)="/static/[^"]+\.(css|js)"', line):
                offenders.append(f"{template.relative_to(TEMPLATES_DIR)}:{lineno}")
    assert not offenders, "Use {{ static_url('...') }} for: " + ", ".join(offenders)
