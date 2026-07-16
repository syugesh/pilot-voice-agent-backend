# Trip Planner (Flights, Hotels, Trains)

Voice- and card-driven search for flights, hotels, and trains — "customer care" in the sidebar. Every result is real, live data; nothing is invented when a search comes up empty.

## Where the code lives

| Layer | File |
|---|---|
| Search + booking logic | `backend/tools/flight_booking.py` — the single largest tool file, covers all three service types |
| Frontend | `frontend/src/components/transcript/CustomerCareView.tsx` — result cards, star/price filters, sort |
| Real data sources | Tavily (live web search, tried first), Amadeus (real hotel/flight inventory + prices), Google Places (real names/ratings/photos) — all real APIs, no mocked responses |

## Design principle: never fabricate

This is the one rule enforced everywhere in this file: a rating, price, amenity, or review is only ever shown if it was actually found in a real source. When nothing real is found, the response says so honestly ("I couldn't find any live hotel listings near X") rather than inventing a plausible-looking result. The one known exception is a legacy MCP fallback path inside `_flight_search_impl` (hardcoded Chennai hotel list + a synthetic `₹{5500 + idx*1500}` price generator) — this still exists and is reachable when the real search returns nothing, and conflicts with the rest of the file's philosophy. Flagged for removal, not yet removed.

## Hotels: star category vs. review score

A hotel's official star **classification** (e.g. "a genuine 5-star property") and its guest **review score** (e.g. "8.4/10") are different things — real review scores almost never reach a literal 5.0/5.0, so filtering "5 star hotels" against review scores returns near-zero results. `_filter_hotels_by_star()` fixes this with a three-tier strategy:

1. Hotels with a confirmed star-category match (from an explicit "5-star hotel" claim in the source text) are shown first.
2. Padded with the next-best real alternatives (by review rating) up to at least 15 total results, so a query always returns a full page when that many real hotels exist.
3. If literally nothing has a confirmed category, falls back to the highest-rated real hotels found, with an honest spoken-reply caveat (`rating_fallback` flag) rather than silently claiming a category match.

Each hotel result also carries: up to 3 real, attributed guest review quotes (`_iter_review_quotes`, several phrasings supported), amenities actually found near its listing, a real per-hotel photo (fetched via a dedicated Tavily image search, capped concurrency of 5 to avoid timeouts), and a price labeled honestly as "total for room/night" (never a fabricated per-person breakdown, since no data source here provides one).

## Trains: connecting routes

Direct trains are searched first (`_search_trains_real`); if none parse from live results, a connecting interchange city is derived from a live search (never a hardcoded hub list) and up to 4 candidate hubs are tried in turn until one actually has real trains on both legs — not just the first candidate, since an incidental capitalized phrase in the search answer can geocode successfully without being the correct junction.

## Frontend gotchas already fixed

- Cards vs. raw text: `parseHotelsFromText`/`parseFlightsFromText`/`parseTrainsFromText` (in `transcript/helpers.tsx`) must match the *actual* backend text format exactly, or raw unstructured text renders alongside the cards instead of being hidden.
- The client-side star-rating filter checkboxes had the same star-category-vs-review-score conflation bug as the backend; fixed in `parseRatingNum`.
- The price-range slider silently rendered a meaningless "0 to 1" range when no result in the current search had a parsable price (common now, since "price on request" is honest and frequent) — it's hidden entirely in that case instead.
