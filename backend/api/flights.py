import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from backend.core.deps import BookingRateLimiter, get_current_user
from backend.db.models import User

router = APIRouter()
logger = logging.getLogger("pilot.api.flights")

# Rate limiter singletons — instantiated once, reused across requests
_rl_search = BookingRateLimiter("search")
_rl_book   = BookingRateLimiter("book")


class SearchReq(BaseModel):
    origin: str
    destination: str
    date: str
    passengers: int = 1
    service_type: str = "flights"
    train_class: str = ""
    session_id: str | None = None
    # Real coordinates from the browser's Geolocation API, for hotel "near
    # me" search — never a guessed/default city (see flight_search).
    lat: float | None = None
    lng: float | None = None
    check_out: str | None = None
    min_rating: float | None = None


class BookReq(BaseModel):
    flight_id: str
    passenger_name: str = ""   # optional — if omitted, derived from JWT
    session_id: str


class TrainStopsReq(BaseModel):
    train_code: str
    train_name: str = ""
    origin: str = ""
    destination: str = ""


@router.post("/search")
async def search(
    req: SearchReq,
    user: User = Depends(get_current_user),
    _rl: None = Depends(_rl_search),
):
    """
    Search for flights / hotels / trains.
    Requires a valid Bearer token. Rate-limited to 10 requests per user per minute.
    """
    from backend.tools.flight_booking import flight_search

    logger.info(f"Search requested by user_id={user.id} ({user.email}): {req.service_type} {req.origin}->{req.destination}")

    query = f"search {req.service_type} from {req.origin} to {req.destination} on {req.date}"
    if req.service_type == "hotels":
        query = f"search hotels in {req.origin} on {req.date}"
    if req.service_type == "trains" and req.train_class:
        query = f"search trains from {req.origin} to {req.destination} on {req.date} class {req.train_class}"

    session_id = req.session_id or "api"

    return await flight_search(
        {
            "origin": req.origin,
            "destination": req.destination,
            "date": req.date,
            "service_type": req.service_type,
            "query": query,
            "train_class": req.train_class,
            "passengers": req.passengers,
            "lat": req.lat,
            "lng": req.lng,
            "check_out": req.check_out,
            "min_rating": req.min_rating,
        },
        session_id,
    )


@router.post("/book")
async def book(
    req: BookReq,
    user: User = Depends(get_current_user),
    _rl: None = Depends(_rl_book),
):
    """
    Book a flight / hotel / train ticket.
    Requires a valid Bearer token. Passenger name is always derived from the
    authenticated user's JWT to prevent booking under arbitrary names.
    Rate-limited to 5 requests per user per minute.
    """
    from backend.tools.flight_booking import flight_book

    # Policy: always use the authenticated user's real name — ignore client-supplied name
    verified_name = user.name
    verified_email = user.email

    logger.info(
        f"Booking requested by user_id={user.id} ({verified_email}) "
        f"for flight_id={req.flight_id}"
    )

    return await flight_book(
        {
            "flight_id": req.flight_id,
            "passenger_name": verified_name,
            "passenger_email": verified_email,
        },
        req.session_id,
    )


@router.post("/train-stops")
async def train_stops(
    req: TrainStopsReq,
    user: User = Depends(get_current_user),
    _rl: None = Depends(_rl_search),
):
    """
    On-demand full-route stop list for one train, fetched only when a user
    expands a train card in the UI (not part of the main search results,
    which don't reliably carry named intermediate stops — see
    search_train_full_route's docstring). Requires a valid Bearer token.
    """
    from backend.tools.flight_booking import search_train_full_route

    logger.info(f"Train stops requested by user_id={user.id}: {req.train_code} {req.train_name}")
    return await search_train_full_route(req.train_code, req.train_name, req.origin, req.destination)

