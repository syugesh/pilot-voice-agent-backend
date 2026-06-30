from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional
router = APIRouter()

class SearchReq(BaseModel):
    origin: str; destination: str; date: str; passengers: int = 1

class BookReq(BaseModel):
    flight_id: str; passenger_name: str; session_id: str

@router.post("/search")
async def search(req: SearchReq):
    from tools.flight_booking import flight_search
    return await flight_search({"origin": req.origin, "destination": req.destination, "date": req.date}, "api")

@router.post("/book")
async def book(req: BookReq):
    from tools.flight_booking import flight_book
    return await flight_book({"flight_id": req.flight_id, "passenger_name": req.passenger_name}, req.session_id)



# from fastapi import APIRouter
# from pydantic import BaseModel
# from typing import Optional
# router = APIRouter()

# class SearchReq(BaseModel):
#     origin: str; destination: str; date: str; passengers: int = 1

# class BookReq(BaseModel):
#     flight_id: str; passenger_name: str; session_id: str

# @router.post("/search")
# async def search(req: SearchReq):
#     from tools.flight_booking import flight_search
#     return await flight_search({"origin": req.origin, "destination": req.destination, "date": req.date}, "api")

# @router.post("/book")
# async def book(req: BookReq):
#     from tools.flight_booking import flight_book
#     return await flight_book({"flight_id": req.flight_id, "passenger_name": req.passenger_name}, req.session_id)
