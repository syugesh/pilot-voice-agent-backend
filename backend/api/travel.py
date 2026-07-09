from fastapi import APIRouter
from pydantic import BaseModel
router = APIRouter()

class SearchReq(BaseModel):
    origin: str; destination: str; date: str; passengers: int = 1

class BookReq(BaseModel):
    flight_id: str; passenger_name: str; session_id: str

@router.post("/search")
async def search(req: SearchReq):
    from tools.travel_planner import travel_search
    return await travel_search({"origin": req.origin, "destination": req.destination, "date": req.date}, "api")

@router.post("/book")
async def book(req: BookReq):
    from tools.travel_planner import flight_book
    return await flight_book({"flight_id": req.flight_id, "passenger_name": req.passenger_name}, req.session_id)
