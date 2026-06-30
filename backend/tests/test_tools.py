import pytest
from tools.knowledge import kb_search
from tools.flight_booking import flight_search, flight_book
from tools.tickets import ticket_create

@pytest.mark.asyncio
async def test_kb_search_hit():
    res = await kb_search({"query": "baggage allowance"}, "s1")
    assert res["status"] == "ok"
    assert len(res["results"]) > 0

@pytest.mark.asyncio
async def test_kb_search_miss():
    res = await kb_search({"query": "xyzunknown123"}, "s1")
    assert res["status"] == "ok"
    assert isinstance(res["results"], list)

@pytest.mark.asyncio
async def test_flight_search():
    res = await flight_search({"origin": "JFK", "destination": "LAX", "date": ""}, "s1")
    assert res["status"] == "ok"
    assert len(res["flights"]) >= 1

@pytest.mark.asyncio
async def test_flight_book_not_found():
    res = await flight_book({"flight_id": "NOTEXIST", "passenger_name": "Test"}, "s1")
    assert res["status"] == "error"
