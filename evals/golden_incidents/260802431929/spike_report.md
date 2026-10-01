# Spike Report — Trip 260802431929

## Spike 2: SOAP XML parsing — PASS
- `SUPPLIER_BOOK` response is SOAP XML (OTA_AirBookRS)
- lxml can parse it; baggage in `BaggageRequest/@baggageCode="30 Kg 1 Piece"`
- PNR in `BookingReferenceID/@ID="6FS8S6"`
- E-ticket in `ETicketInfomation/@eTicketNo`
- `SUPPLIER_PRICE_QUOTE` and `SUPPLIER_ANCILLARY_BAGGAGE` also SOAP

## Spike 4: Log API reachability — PASS
- `bqapi.cleartripcorp.me` reachable from this machine over VPN
- `tripId` parameter works (not just `iId`)
- Token validation: none (DummyToken works)
- CORS headers required: `origin`, `referer`
- Response: 200, JSON, `status: 1`
- 126 air_api_call entries, 99 air_book entries
- File download: gzipped, decompresses cleanly

## Spike 1: Gemini Flash tool-calling — BLOCKED
Needs GEMINI_API_KEY.

## Spike 3: Gmail API — BLOCKED
Needs GEMINI_API_KEY / Gmail credentials.

## Key findings for plan update
- API accepts both `tripId` and `iId` parameters
- Headers must include `origin: https://statsui.cleartripcorp.me` and `referer`
- Token/user fields accept any value (no auth validation)
- `files_list` count is 1 (not the ~300 mentioned in plan — may be paginated or structured differently)
