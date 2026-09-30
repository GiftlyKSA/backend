"""Content-versioned dashboard styles served from the running application build."""

from hashlib import sha256
from pathlib import Path

STYLESHEET = (Path(__file__).parent / "static" / "admin.css").read_bytes()
STYLESHEET_VERSION = sha256(STYLESHEET).hexdigest()[:16]
STYLESHEET_URL = f"/v1/admin/admin/assets/admin.{STYLESHEET_VERSION}.css"
