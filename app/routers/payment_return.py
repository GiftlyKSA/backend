"""Public Dhamen browser return and mobile app association resources."""

from __future__ import annotations

import base64
import hashlib
import re

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

from app.core.deps import get_settings

router = APIRouter(tags=["payment return"])

_DEEP_LINK = "giftly://payment/return"
_AUTO_OPEN_SCRIPT = f'window.location.replace("{_DEEP_LINK}");'
_AUTO_OPEN_CSP_HASH = base64.b64encode(
    hashlib.sha256(_AUTO_OPEN_SCRIPT.encode("utf-8")).digest()
).decode("ascii")
_APPLE_APP_IDENTIFIER = re.compile(r"[A-Z0-9]{1,32}\.[A-Za-z0-9]+(?:[.-][A-Za-z0-9]+)*\Z")
_ANDROID_APPLICATION_ID = re.compile(r"[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)+\Z")
_SHA256_FINGERPRINT = re.compile(r"(?:[A-F0-9]{2}:){31}[A-F0-9]{2}\Z")
_ASSOCIATION_HEADERS = {
    "Cache-Control": "public, max-age=300",
    "X-Content-Type-Options": "nosniff",
}


@router.get(
    "/payment/return",
    response_class=HTMLResponse,
    name="payment_return",
    summary="Return from hosted checkout to Giftly",
    description=(
        "Public browser fallback only. It opens the fixed Giftly app link and never "
        "interprets query parameters or changes payment state. Check payment status "
        "through the authenticated payment-session refresh endpoint."
    ),
)
def payment_return(request: Request) -> HTMLResponse:
    """Offer a fixed deep-link fallback without interpreting payment query values."""
    accept_language = request.headers.get("accept-language", "").split(",", maxsplit=1)[0]
    is_arabic = accept_language.strip().lower().split("-", maxsplit=1)[0] == "ar"
    if is_arabic:
        language = "ar"
        direction = "rtl"
        title = "العودة إلى جفتلي"
        message = "يمكنك العودة إلى تطبيق جفتلي للتحقق من حالة الدفع. قد يستغرق تحديث الحالة لحظات."
        button = "افتح جفتلي"
        fallback = "إذا لم يفتح التطبيق تلقائيًا، اضغط على الزر أدناه."
    else:
        language = "en"
        direction = "ltr"
        title = "Return to Giftly"
        message = (
            "Return to Giftly to check your payment status. It may take a few moments to update."
        )
        button = "Open Giftly"
        fallback = "If the app does not open automatically, use the button below."

    page = f"""<!doctype html>
<html lang="{language}" dir="{direction}">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="referrer" content="no-referrer">
  <title>{title}</title>
  <style>
    body {{ margin: 0; min-height: 100vh; display: grid; place-items: center;
      background: #f6f0fa; color: #201b26; font: 16px/1.6 system-ui, sans-serif; }}
    main {{ box-sizing: border-box; width: min(92vw, 28rem); padding: 2rem;
      border: 1px solid #e9e2ee; border-radius: 1rem; background: #fff;
      text-align: center; box-shadow: 0 1rem 3rem #201b2614; }}
    h1 {{ margin: 0 0 0.75rem; color: #68318e; font-size: 1.5rem; }}
    p {{ margin: 0 0 1.25rem; }}
    .hint {{ color: #746b7b; font-size: 0.9rem; }}
    a {{ display: inline-block; padding: 0.7rem 1.4rem; border-radius: 0.65rem;
      background: #68318e; color: #fff; font-weight: 700; text-decoration: none; }}
    a:focus-visible {{ outline: 3px solid #c8afe7; outline-offset: 3px; }}
  </style>
</head>
<body>
  <main>
    <h1>{title}</h1>
    <p>{message}</p>
    <p class="hint">{fallback}</p>
    <a href="{_DEEP_LINK}">{button}</a>
  </main>
  <script>{_AUTO_OPEN_SCRIPT}</script>
</body>
</html>"""
    response = HTMLResponse(page, headers={"Cache-Control": "no-store"})
    response.headers["Content-Security-Policy"] = (
        "default-src 'none'; "
        f"script-src 'sha256-{_AUTO_OPEN_CSP_HASH}'; "
        "style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
    )
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


def _association_error() -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content={"detail": "App association is not configured."},
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )


@router.get(
    "/.well-known/apple-app-site-association",
    include_in_schema=False,
    name="apple_app_site_association",
)
def apple_app_site_association(request: Request) -> JSONResponse:
    """Serve the exact iOS payment-return association when configured."""
    settings = get_settings(request)
    app_identifier = settings.APPLE_APP_IDENTIFIER
    if not app_identifier or not _APPLE_APP_IDENTIFIER.fullmatch(app_identifier):
        return _association_error()

    return JSONResponse(
        content={
            "applinks": {
                "apps": [],
                "details": [{"appID": app_identifier, "components": [{"/": "/payment/return"}]}],
            }
        },
        headers=_ASSOCIATION_HEADERS,
    )


@router.get("/.well-known/assetlinks.json", include_in_schema=False, name="android_asset_links")
def android_asset_links(request: Request) -> JSONResponse:
    """Serve Android's origin association only when the signing fingerprints are valid."""
    settings = get_settings(request)
    application_id = settings.ANDROID_APPLICATION_ID
    raw_fingerprints = settings.ANDROID_SHA256_CERT_FINGERPRINTS
    if not application_id or not _ANDROID_APPLICATION_ID.fullmatch(application_id):
        return _association_error()
    if not raw_fingerprints:
        return _association_error()

    fingerprints = tuple(
        dict.fromkeys(
            value.strip().upper() for value in raw_fingerprints.split(",") if value.strip()
        )
    )
    if not fingerprints or any(not _SHA256_FINGERPRINT.fullmatch(value) for value in fingerprints):
        return _association_error()

    return JSONResponse(
        content=[
            {
                "relation": ["delegate_permission/common.handle_all_urls"],
                "target": {
                    "namespace": "android_app",
                    "package_name": application_id,
                    "sha256_cert_fingerprints": list(fingerprints),
                },
            }
        ],
        headers=_ASSOCIATION_HEADERS,
    )
