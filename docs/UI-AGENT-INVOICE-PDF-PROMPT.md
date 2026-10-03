# Invoice PDF and VAT UI handoff

```text
Update Giftly invoice screens to use server-authoritative totals.

VAT is applied only to discounted item amounts. Courier/delivery and service fees
have zero VAT. Do not recompute historical invoice totals in the UI.
Example: items 100.00 + courier 20.00 + service 6.00 + item VAT 15.00 = SAR 141.00.
Discount example: items 500.00 + courier 100.00 + service 30.00, promo discount
60.00 allocated as 50.00 items and 10.00 courier; item VAT 67.50; total 637.50.
Money remains decimal strings; invoice request and JSON response fields are unchanged.

GET /api/invoices/{invoice_id}/pdf
Authorization: Bearer <access_token>
Who: the owning customer or assigned eligible courier; no other user can access it.
Input: invoice_id is a UUID path string. No JSON body or query parameters.
Success: HTTP 200, Content-Type application/pdf, binary bytes (not JSON).
Content-Disposition supplies the filename; Cache-Control is private, no-store.
Fetch with the Authorization header, then save/share the returned blob.
Never put the access token in a download URL. Never share a public invoice URL.
Use the current invoice ID returned by the server; repair scripts may create a new revision.
401 invalid/expired authentication; 403 ineligible account/role; 404 absent or foreign invoice;
422 invalid UUID; 429 rate limit. Errors use the normal JSON error envelope.

The PDF uses English labels, UTC timestamp labels, stored lines, fees, discounts,
VAT, and total. Non-ASCII item titles use an English Item <position> label.
Historic invoice PDFs reproduce stored amounts, including the old VAT if not repaired.

After confirmed payment the backend's receipt worker sends the customer's email
an English invoice with PDF attachment through sndr.sh. The client must not send
email itself or treat a browser redirect as proof of payment. Customers without
email retain a pending receipt; PDF download remains available.

At Saudi midnight, NEW unaccepted orders with delivery dates before the new day
are cancelled by a system task. Accepted orders are preserved. Refresh order state
when opening old orders; existing order WebSockets also poll authoritative state.
```
