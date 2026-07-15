"""
Amadeus Self-Service API client — real hotel search (name, address, live
price, star rating) near a geocode. Replaces the fabricated hotel data
previously used as a fallback in tools/flight_booking.py.

Two-step flow, per Amadeus's actual API shape:
  1. Hotel List by geocode -> hotel IDs/names/distance near a lat/lng (no price)
  2. Hotel Search (offers) for those IDs -> real prices + availability

Self-service (free tier) keys run against the *test* environment, which has
real but limited city coverage (mostly major global markets) — a search for
a city Amadeus's sandbox doesn't cover will legitimately come back empty.
That's surfaced honestly by returning [] rather than inventing results;
callers should say "no hotels found" rather than padding with fake data.
"""
import logging
import time

import httpx

from backend.core.config import settings

logger = logging.getLogger("pilot.amadeus")

_BASE_URL = "https://test.api.amadeus.com"

_token_cache: dict = {"access_token": None, "expires_at": 0.0}


async def _get_token() -> str | None:
    if not (settings.AMADEUS_CLIENT_ID and settings.AMADEUS_CLIENT_SECRET):
        return None

    now = time.time()
    if _token_cache["access_token"] and now < _token_cache["expires_at"] - 30:
        return _token_cache["access_token"]

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{_BASE_URL}/v1/security/oauth2/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": settings.AMADEUS_CLIENT_ID,
                    "client_secret": settings.AMADEUS_CLIENT_SECRET,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=10.0,
            )
            if resp.status_code != 200:
                logger.warning(f"Amadeus token request failed: {resp.status_code} {resp.text[:200]}")
                return None
            data = resp.json()
            _token_cache["access_token"] = data["access_token"]
            _token_cache["expires_at"] = now + int(data.get("expires_in", 1800))
            return _token_cache["access_token"]
    except Exception as e:
        logger.warning(f"Amadeus token request error: {e}")
        return None


async def _hotel_ids_near(client: httpx.AsyncClient, token: str, lat: float, lng: float, radius_km: int) -> list[dict]:
    resp = await client.get(
        f"{_BASE_URL}/v1/reference-data/locations/hotels/by-geocode",
        params={"latitude": lat, "longitude": lng, "radius": radius_km, "radiusUnit": "KM"},
        headers={"Authorization": f"Bearer {token}"},
        timeout=10.0,
    )
    if resp.status_code != 200:
        logger.info(f"Amadeus hotel-list-by-geocode: {resp.status_code} {resp.text[:200]}")
        return []
    return resp.json().get("data", [])


async def _hotel_offers(client: httpx.AsyncClient, token: str, hotel_ids: list[str],
                         check_in: str, check_out: str, adults: int) -> list[dict]:
    if not hotel_ids:
        return []
    resp = await client.get(
        f"{_BASE_URL}/v3/shopping/hotel-offers",
        params={
            "hotelIds": ",".join(hotel_ids[:20]),  # API cap on IDs per request
            "checkInDate": check_in,
            "checkOutDate": check_out,
            "adults": adults,
            "bestRateOnly": "true",
        },
        headers={"Authorization": f"Bearer {token}"},
        timeout=15.0,
    )
    if resp.status_code != 200:
        logger.info(f"Amadeus hotel-offers: {resp.status_code} {resp.text[:200]}")
        return []
    return resp.json().get("data", [])


async def search_hotels_near(
    lat: float, lng: float, check_in: str, check_out: str,
    adults: int = 1, radius_km: int = 10, min_rating: int | None = None,
) -> list[dict]:
    """Real hotels with real prices near (lat, lng). Returns [] — never
    fabricated data — if Amadeus isn't configured, unreachable, or has no
    inventory for this area/date (common outside major cities on the free
    test tier)."""
    token = await _get_token()
    if not token:
        return []

    try:
        async with httpx.AsyncClient() as client:
            candidates = await _hotel_ids_near(client, token, lat, lng, radius_km)
            if not candidates:
                return []
            hotel_ids = [h["hotelId"] for h in candidates if h.get("hotelId")]
            offers_data = await _hotel_offers(client, token, hotel_ids, check_in, check_out, adults)
    except Exception as e:
        logger.warning(f"Amadeus hotel search error: {e}")
        return []

    dist_by_id = {h["hotelId"]: h.get("distance", {}).get("value") for h in candidates if h.get("hotelId")}

    results = []
    for entry in offers_data:
        hotel = entry.get("hotel", {})
        offers = entry.get("offers", [])
        if not offers:
            continue
        offer = offers[0]
        price = offer.get("price", {})
        rating_raw = hotel.get("rating")
        try:
            rating = int(rating_raw) if rating_raw is not None else None
        except (TypeError, ValueError):
            rating = None
        if min_rating is not None and (rating is None or rating < min_rating):
            continue

        address = hotel.get("address", {})
        address_lines = ", ".join(filter(None, [
            *(address.get("lines") or []),
            address.get("cityName"),
        ]))

        results.append({
            "id": hotel.get("hotelId"),
            "name": hotel.get("name") or "Hotel",
            "location": address_lines or None,
            "distance_km": dist_by_id.get(hotel.get("hotelId")),
            "rating": rating,  # official star category, 1-5, when Amadeus has it — None if unknown
            "price": price.get("total"),
            "currency": price.get("currency"),
            "check_in": check_in,
            "check_out": check_out,
            "room_description": (offer.get("room", {}).get("description", {}) or {}).get("text"),
        })

    results.sort(key=lambda h: float(h["price"]) if h.get("price") else float("inf"))
    return results
