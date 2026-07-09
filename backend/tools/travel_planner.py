"""Travel planner tools — flight/hotel/train search (real-time via Node MCP,
or mock fallback) plus flight booking."""
import asyncio, uuid, logging, datetime, os, re, json
import httpx
from core.config import settings

logger = logging.getLogger("pilot.tools.travel")

CITY_TO_CODE: dict[str, str] = {
    "delhi": "DEL", "new delhi": "DEL",
    "mumbai": "BOM", "bombay": "BOM",
    "bangalore": "BLR", "bengaluru": "BLR",
    "chennai": "MAA", "madras": "MAA",
    "kolkata": "CCU", "calcutta": "CCU",
    "hyderabad": "HYD",
    "ahmedabad": "AMD",
    "pune": "PNQ",
    "goa": "GOI",
    "kochi": "COK", "cochin": "COK",
    "jaipur": "JAI",
    "lucknow": "LKO",
    "new york": "JFK", "nyc": "JFK",
    "los angeles": "LAX",
    "boston": "BOS",
    "san francisco": "SFO",
    "london": "LHR",
    "dubai": "DXB",
    "singapore": "SIN",
    "tokyo": "NRT",
}

_MOCK_FLIGHTS = [
    {"id": "AI101", "airline": "Air India", "origin": "DEL", "destination": "BOM", "departure": "06:00", "arrival": "08:05", "price": 4200,  "currency": "INR", "seats": 14},
    {"id": "6E201", "airline": "IndiGo",    "origin": "DEL", "destination": "BOM", "departure": "10:30", "arrival": "12:40", "price": 3650,  "currency": "INR", "seats": 6},
    {"id": "SG301", "airline": "SpiceJet",  "origin": "DEL", "destination": "BOM", "departure": "18:45", "arrival": "20:55", "price": 3100,  "currency": "INR", "seats": 22},
    {"id": "AI102", "airline": "Air India", "origin": "BOM", "destination": "DEL", "departure": "07:15", "arrival": "09:20", "price": 4500,  "currency": "INR", "seats": 9},
    {"id": "AI501", "airline": "Air India", "origin": "DEL", "destination": "BLR", "departure": "08:20", "arrival": "11:05", "price": 5100,  "currency": "INR", "seats": 18},
    {"id": "6E601", "airline": "IndiGo",    "origin": "BLR", "destination": "DEL", "departure": "09:00", "arrival": "11:45", "price": 4800,  "currency": "INR", "seats": 7},
    {"id": "FL001", "airline": "Delta",     "origin": "JFK", "destination": "LAX", "departure": "08:00", "arrival": "11:30", "price": 299,   "currency": "USD", "seats": 12},
    {"id": "EK501", "airline": "Emirates",  "origin": "DEL", "destination": "DXB", "departure": "03:25", "arrival": "05:30", "price": 18500, "currency": "INR", "seats": 20},
]


def _iata(name: str) -> str:
    if not name:
        return ""
    n = name.strip().lower()
    return CITY_TO_CODE.get(n, name.strip().upper())


def _price_str(price: float, currency: str) -> str:
    return f"₹{price:,.0f}" if currency in ("INR", "₹") else f"${price:,.0f}"


# Airline detection: (keyword in url/title, display name, IATA code)
_AIRLINE_KEYWORDS = [
    ("goindigo",  "IndiGo",    "6E"),
    ("indigo",    "IndiGo",    "6E"),
    ("airindia",  "Air India", "AI"),
    ("air india", "Air India", "AI"),
    ("spicejet",  "SpiceJet",  "SG"),
    ("akasa",     "Akasa Air", "QP"),
    ("vistara",   "Vistara",   "UK"),
    ("makemytrip","MakeMyTrip","MM"),
    ("easemytrip","EaseMyTrip","EM"),
    ("ixigo",     "Ixigo",     "IX"),
    ("expedia",   "Expedia",   "EX"),
    ("cleartrip", "Cleartrip", "CT"),
]

_PRICE_RE = re.compile(r'[₹₨]\s*(\d[\d,]+)|Rs\.?\s*(\d[\d,]+)', re.IGNORECASE)
_DEP_SLOTS = ["06:00", "09:15", "12:30", "15:45", "19:00"]


def _parse_tavily_to_flights(content_data: dict, origin: str, destination: str) -> list[dict]:
    """Convert Tavily web results into structured flight cards using actual result URLs."""
    results = content_data.get("results", [])
    flights = []

    for i, r in enumerate(results[:5]):
        title   = r.get("title", "")
        snippet = r.get("snippet", "") or r.get("content", "")
        url     = r.get("url", "")          # actual page from Tavily — use as booking link
        combined = (url + " " + title).lower()

        # Detect display name from URL/title
        airline, code = "Airline", "FL"
        for kw, name, iata in _AIRLINE_KEYWORDS:
            if kw in combined:
                airline, code = name, iata
                break

        # Extract cheapest INR price (skip USD)
        price = "—"
        for m in _PRICE_RE.finditer(snippet + " " + title):
            raw = (m.group(1) or m.group(2) or "").replace(",", "")
            if raw.isdigit() and 1000 < int(raw) < 200000:
                price = f"₹{int(raw):,}"
                break

        dep = _DEP_SLOTS[i % len(_DEP_SLOTS)]
        dep_h, dep_m = int(dep[:2]), int(dep[3:])
        total_m = dep_h * 60 + dep_m + 165          # ~2h45m domestic average
        arr = f"{(total_m // 60) % 24:02d}:{total_m % 60:02d}"

        flights.append({
            "airline":     airline,
            "flight":      f"{code}-{100 + i * 101}",
            "origin":      origin,
            "destination": destination,
            "departure":   dep,
            "arrival":     arr,
            "price":       price,
            "book_url":    url,             # live URL from Tavily
        })

    # Sort cheapest first
    def _price_val(f: dict) -> int:
        p = f["price"].replace("₹", "").replace(",", "").strip()
        return int(p) if p.isdigit() else 999999

    flights.sort(key=_price_val)
    return flights


async def call_mcp_tool_async(server_cmd: str, server_args: list[str], tool_name: str, arguments: dict) -> dict:
    """Connects to an external Node MCP server running over stdio, initializes session, and calls specified tool."""
    try:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
    except ImportError:
        logger.warning("mcp SDK not installed. Fallback to mock data.")
        return {"error": "mcp_not_installed"}

    env = os.environ.copy()
    # Inject keys from pydantic settings — they are NOT in os.environ by default
    from core.config import settings
    if settings.TAVILY_API_KEY:
        env["TAVILY_API_KEY"] = settings.TAVILY_API_KEY

    server_params = StdioServerParameters(
        command=server_cmd,
        args=server_args,
        env=env
    )

    try:
        logger.info(f"Connecting to MCP Server: {server_cmd} {' '.join(server_args)}")
        async with stdio_client(server_params) as (read, write):
            async with ClientSession(read, write) as session:
                logger.info("Initializing MCP Session...")
                await session.initialize()

                logger.info(f"Invoking tool '{tool_name}' with args {arguments}...")
                result = await asyncio.wait_for(
                    session.call_tool(tool_name, arguments=arguments),
                    timeout=20.0
                )
                logger.info(f"MCP invocation completed: {result}")

                texts = [c.text for c in result.content if hasattr(c, 'text')]
                try:
                    if texts:
                        return {"status": "success", "content": json.loads(texts[0])}
                except Exception:
                    pass
                return {"status": "success", "content": texts}
    except asyncio.TimeoutError:
        logger.warning("MCP tool call timed out after 20s — using static fallback")
        return {"error": "timeout"}
    except Exception as e:
        logger.error(f"MCP Error: {e}")
        return {"error": str(e)}


async def travel_search(args: dict, session_id: str) -> dict:
    """Searches flights, hotels, or trains — service_type is auto-detected
    from keywords in the query (see `services` dict below)."""
    from core.session_state import get_state
    state = get_state(session_id)

    # ── Extract search details ──
    query = args.get("query") or args.get("synopsis") or ""
    
    # Heuristic extraction of cities
    known_cities = {
        "mumbai", "delhi", "chennai", "bangalore", "hyderabad",
        "kolkata", "pune", "ahmedabad", "jaipur", "lucknow"
    }
    words = re.findall(r"[a-zA-Z]+", query.lower())
    cities = [w.title() for w in words if w in known_cities]
    
    # Extract locations, prioritizing manual UI overrides first. No longer hardcodes Mumbai/Delhi if missing!
    origin = state.typed_origin or args.get("origin") or (cities[0] if len(cities) > 0 else "")
    destination = state.typed_destination or args.get("destination") or (cities[1] if len(cities) > 1 else "")

    # ── Detect Service Type ── (moved above the origin/destination check
    # below: a hotel search only needs ONE city — "hotels in Mumbai" is a
    # complete request with no "departure city" concept, unlike
    # flights/trains — so what's required depends on which this is.)
    services = {
        "flights": ["flight", "flights", "airport"],
        "hotels": ["hotel", "room", "stay", "lodging"],
        "trains": ["train", "rail", "irctc"],
        "cabs": ["cab", "taxi", "uber", "ola", "rapido"]
    }
    service_type = "flights"
    for svc, keywords in services.items():
        if any(kw in query.lower() for kw in keywords):
            service_type = svc
            break

    if service_type == "hotels":
        if not origin and not destination:
            return {
                "status": "error",
                "message": "missing_parameters",
                "spoken_reply": "Which city would you like to find a hotel in?",
            }
        # A single mentioned city is enough — `cities[0]` naturally lands in
        # `origin`, and the fallback hotel data below keys location off it.
        origin = origin or destination
    elif not origin or not destination:
        msg = "I can definitely search that for you! Could you please tell me which city you are departing from, and where you are headed?"
        if not origin and destination:
            msg = f"I've got your destination as {destination}! Which city are you departing from?"
        elif origin and not destination:
            msg = f"I see you are departing from {origin}! What is your destination city?"

        return {
            "status": "error",
            "message": "missing_parameters",
            "spoken_reply": msg
        }

    # Resolve date: manual UI override → args from LLM → ISO in query → natural language → today
    from services.front_llm import _parse_natural_date
    date_match = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", query)
    if state.typed_date:
        date = state.typed_date
    elif args.get("date"):
        date = args["date"]
    elif date_match:
        date = date_match.group(1)
    else:
        date = _parse_natural_date(query) or datetime.date.today().isoformat()

    # Normalize name values
    origin_iata = _iata(origin)
    dest_iata = _iata(destination)

    logger.info(f"Travel Search: service_type={service_type}, origin={origin}, destination={destination}, query={query}")

    # ── Call Node MCP Server if configured (flights only) ──
    # The configured MCP tool (PILOT_MCP_TOOL, typically "search_flights_web")
    # and _parse_tavily_to_flights below are both flight-specific — the tool
    # name is hardcoded per-deployment (not swapped per service_type) and the
    # parser always emits flight-shaped fields (airline, departure, book_url,
    # ...) regardless of what was actually searched for. Rather than surface
    # a flight web search mislabeled as hotel/train results, hotels and
    # trains always use the well-shaped static fallback data below.
    mcp_response = {}
    if settings.PILOT_MCP_ARGS and service_type == "flights":
        MCP_SERVER_COMMAND = settings.PILOT_MCP_COMMAND or "node"
        MCP_SERVER_ARGS = [settings.PILOT_MCP_ARGS]
        
        # Prioritize the PILOT_MCP_TOOL env/settings if configured
        if settings.PILOT_MCP_TOOL:
            MCP_TOOL_NAME = settings.PILOT_MCP_TOOL
        else:
            MCP_TOOL_MAP = {
                "flights": "get_flights",
                "hotels": "get_hotels",
                "trains": "get_trains",
                "cabs": "get_cabs"
            }
            MCP_TOOL_NAME = MCP_TOOL_MAP.get(service_type, "get_flights")

        mcp_arguments = {
            "serviceType": service_type,
            "origin": origin,
            "destination": destination,
            "query": query
        }

        mcp_response = await call_mcp_tool_async(
            server_cmd=MCP_SERVER_COMMAND,
            server_args=MCP_SERVER_ARGS,
            tool_name=MCP_TOOL_NAME,
            arguments=mcp_arguments
        )

    # ── Check MCP response and fallback if needed ──
    if mcp_response and mcp_response.get("status") == "success":
        content_data = mcp_response.get("content")
        logger.info(f"Successfully retrieved travel info via MCP: {content_data}")
        
        # If it's a dict, we can construct standard response
        if isinstance(content_data, dict):
            summary = content_data.get("summary", "")
            source  = content_data.get("source", "web")
            structured_flights = _parse_tavily_to_flights(content_data, origin, destination)
            spoken = f"According to real-time search: {summary or 'I found some options for you.'}"
            return {
                "status": "ok",
                "service_type": service_type,
                "origin": origin,
                "destination": destination,
                "date": date,
                "results": structured_flights,
                "source": source,
                "spoken_reply": spoken,
            }
        else:
            # If string list, join it
            joined_str = " ".join(content_data) if isinstance(content_data, list) else str(content_data)
            return {
                "status": "ok",
                "service_type": service_type,
                "origin": origin,
                "destination": destination,
                "date": date,
                "raw_data": joined_str,
                "spoken_reply": joined_str
            }

    # ── Static fallback data ──
    fallback_data = {
        "flights": [
            {"airline": "Akasa Air", "flight": "QP-1374", "departure": "14:30", "price": "₹3600", "customerCare": "1800-102-3333"},
            {"airline": "SpiceJet", "flight": "SG-157", "departure": "09:15", "price": "₹3900", "customerCare": "1800-102-2333"},
            {"airline": "IndiGo", "flight": "6E-204", "departure": "06:00", "price": "₹4200", "customerCare": "0124-6173838"},
            {"airline": "Air India", "flight": "AI-865", "departure": "07:30", "price": "₹5800", "customerCare": "1860-233-1407"},
            {"airline": "Vistara", "flight": "UK-935", "departure": "11:00", "price": "₹6500", "customerCare": "1860-233-1407"}
        ],
        "hotels": [
            {"hotel": "Hotel Taj", "location": origin, "phone": "1800-266-7646", "price": "₹8500"},
            {"hotel": "Hotel Oberoi", "location": origin, "phone": "1800-102-2333", "price": "₹6200"}
        ],
        "trains": [
            {"operator": "IRCTC Rajdhani", "phone": "139", "departure": "11:30 AM"},
            {"operator": "Shatabdi Express", "phone": "8010500300", "departure": "06:45 PM"}
        ],
        "cabs": [
            {"operator": "Ola Cabs", "phone": "0120-3355335", "price": "₹900 - ₹1200"},
            {"operator": "Uber", "phone": "080-4685-2190", "price": "₹1000 - ₹1400"}
        ]
    }

    results = fallback_data.get(service_type, [])
    
    # Construct spoken fallback reply dynamically based on origin and destination
    if service_type == "flights":
        spoken = f"I found {len(results)} flights from {origin} to {destination}. Here are the details."
    elif service_type == "hotels":
        spoken = f"Found hotels available in {origin}. The Taj Hotel offers rooms starting at 8,500 Rupees. Customer care number is 1800-266-7646."
    elif service_type == "trains":
        spoken = f"There are 2 train options from {origin} to {destination} today. Rajdhani Express departs at 11:30 AM, or Shatabdi departs at 6:45 PM."
    else:
        spoken = f"Available cab options from {origin} to {destination} include Ola Cabs and Uber, with fares ranging from 900 to 1,500 Rupees."

    return {
        "status": "ok",
        "service_type": service_type,
        "origin": origin,
        "destination": destination,
        "date": date,
        "results": results,
        "source": "fallback — MCP unavailable",
        "spoken_reply": spoken
    }


async def flight_book(args: dict, session_id: str) -> dict:
    fid = args.get("flight_id", "")
    passenger = args.get("passenger_name", "")
    flight = next((f for f in _MOCK_FLIGHTS if f["id"] == fid), None)
    if not flight:
        return {"status": "error", "message": f"Flight {fid} not found in live inventory"}
    ref = f"BK{str(uuid.uuid4())[:6].upper()}"
    logger.info(f"Flight booked: {ref} for {passenger}")
    spoken_reply = f"Successfully booked flight {fid} for {passenger}. Your booking reference is {ref}."
    return {"status": "ok", "booking_ref": ref, "flight": flight, "passenger": passenger, "spoken_reply": spoken_reply}
