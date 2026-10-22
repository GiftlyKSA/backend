"""Public Dhamen browser return page."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["payment return"])


@router.get(
    "/payment/return",
    response_class=HTMLResponse,
    name="payment_return",
    summary="Return from hosted checkout to Giftly",
    description=(
        "Public HTML fallback for Expo Go testing. It instructs the customer to return "
        "to Expo Go manually and never interprets query parameters or changes payment "
        "state. Check payment status through the authenticated refresh endpoint."
    ),
)
def payment_return(request: Request) -> HTMLResponse:
    """Show manual return instructions without interpreting checkout query values."""
    accept_language = request.headers.get("accept-language", "").split(",", maxsplit=1)[0]
    is_arabic = accept_language.strip().lower().split("-", maxsplit=1)[0] == "ar"
    if is_arabic:
        language = "ar"
        direction = "rtl"
        title = "العودة إلى جفتلي"
        message = (
            "ارجع يدويًا إلى Expo Go ثم افتح جفتلي للتحقق من حالة الدفع. "
            "قد يستغرق تحديث الحالة بضع لحظات."
        )
    else:
        language = "en"
        direction = "ltr"
        title = "Return to Giftly"
        message = (
            "Return to Expo Go manually, then check your payment status in Giftly. "
            "It may take a few moments to update."
        )

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
  </style>
</head>
<body>
  <main>
    <h1>{title}</h1>
    <p>{message}</p>
  </main>
</body>
</html>"""
    response = HTMLResponse(page, headers={"Cache-Control": "no-store"})
    response.headers["Content-Security-Policy"] = (
        "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; "
        "form-action 'none'; frame-ancestors 'none'"
    )
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response
