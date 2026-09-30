"""Content-versioned dashboard styles served from the running application build."""

from hashlib import sha256
from pathlib import Path

STYLESHEET = (Path(__file__).parent / "static" / "admin.css").read_bytes()
STYLESHEET_VERSION = sha256(STYLESHEET).hexdigest()[:16]
STYLESHEET_URL = f"/v1/admin/admin/assets/admin.{STYLESHEET_VERSION}.css"
THEME_SCRIPT = (Path(__file__).parent / "static" / "theme.js").read_bytes()
THEME_SCRIPT_VERSION = sha256(THEME_SCRIPT).hexdigest()[:16]
THEME_SCRIPT_URL = f"/v1/admin/admin/assets/theme.{THEME_SCRIPT_VERSION}.js"
