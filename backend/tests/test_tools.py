import pytest
from tools.knowledge import kb_search
from tools.travel_planner import travel_search, flight_book
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
async def test_travel_search_flights():
    res = await travel_search({"origin": "JFK", "destination": "LAX", "date": ""}, "s1")
    assert res["status"] == "ok"
    assert res["service_type"] == "flights"
    assert len(res["results"]) >= 1

@pytest.mark.asyncio
async def test_travel_search_hotels():
    res = await travel_search({"query": "hotels in Mumbai"}, "s1")
    assert res["status"] == "ok"
    assert res["service_type"] == "hotels"
    assert len(res["results"]) >= 1

@pytest.mark.asyncio
async def test_travel_search_trains():
    res = await travel_search({"query": "trains from Delhi to Mumbai"}, "s1")
    assert res["status"] == "ok"
    assert res["service_type"] == "trains"
    assert len(res["results"]) >= 1

@pytest.mark.asyncio
async def test_flight_book_not_found():
    res = await flight_book({"flight_id": "NOTEXIST", "passenger_name": "Test"}, "s1")
    assert res["status"] == "error"
