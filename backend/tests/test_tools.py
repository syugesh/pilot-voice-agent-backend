import pytest

from backend.tools.flight_booking import flight_book, flight_search
from backend.tools.knowledge import kb_search


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
    assert len(res["results"]) >= 1


@pytest.mark.asyncio
async def test_flight_book_not_found():
    res = await flight_book({"flight_id": "NOTEXIST", "passenger_name": "Test"}, "s1")
    assert res["status"] == "ok"
    assert res["source"] == "mock fallback"


from unittest.mock import patch

@pytest.mark.asyncio
@patch("backend.tools.flight_booking._book_duffel")
@patch("backend.core.config.settings.DUFFEL_API_KEY", "test_duffel_key")
async def test_flight_book_duffel_success(mock_book_duffel):
    mock_book_duffel.return_value = {
        "status": "ok",
        "booking_ref": "BKTEST123",
        "order_details": {"id": "ord_123"}
    }

    res = await flight_book({"flight_id": "off_123", "passenger_name": "Tony Stark"}, "s1")
    assert res["status"] == "ok"
    assert res["booking_ref"] == "BKTEST123"
    assert res["source"] == "Duffel API"
    mock_book_duffel.assert_called_once_with("off_123", "Tony Stark")


from backend.tools.flight_booking import flight_checkin

@pytest.mark.asyncio
async def test_flight_checkin_fallback():
    res = await flight_checkin({"booking_ref": "BK12345", "passenger_name": "Tony Stark", "flight_number": "DF-100"}, "s1")
    assert res["status"] == "ok"
    assert res["booking_ref"] == "BK12345"
    assert res["passenger_name"] == "Tony Stark"
    assert res["source"] == "local fallback"


@pytest.mark.asyncio
@patch("backend.tools.flight_booking.call_mcp_tool_async")
@patch("backend.core.config.settings.PILOT_MCP_ARGS", "index2.js")
async def test_flight_checkin_mcp(mock_call_mcp):
    mock_call_mcp.return_value = {
        "status": "success",
        "content": [
            {
                "type": "text",
                "text": '{"status":"success","bookingRef":"BK12345","passengerName":"Tony Stark","message":"Successfully checked in passenger.","guidelines":"Use gate 3."}'
            }
        ]
    }

    res = await flight_checkin({"booking_ref": "BK12345", "passenger_name": "Tony Stark"}, "s1")
    assert res["status"] == "ok"
    assert res["booking_ref"] == "BK12345"
    assert res["source"] == "Node MCP checkin"
    assert res["guidelines"] == "Use gate 3."


@pytest.mark.asyncio
async def test_hotel_booking_success():
    res = await flight_book({"flight_id": "HTL_MUMBAI_101", "passenger_name": "Bruce Wayne", "passenger_email": "bruce@wayne.com"}, "s1")
    assert res["status"] == "ok"
    assert res["booking_ref"].startswith("HTL")
    assert res["passenger"] == "Bruce Wayne"
    assert res["source"] == "Tavily Hotel booking"
    assert "reservation reference is HTL" in res["spoken_reply"]


@pytest.mark.asyncio
async def test_train_booking_success():
    res = await flight_book({"flight_id": "TRN_12603", "passenger_name": "Clark Kent", "passenger_email": "clark@dailyplanet.com"}, "s1")
    assert res["status"] == "ok"
    assert res["booking_ref"].startswith("PNR")
    assert res["passenger"] == "Clark Kent"
    assert res["source"] == "Railways booking"
    assert "Your PNR is PNR" in res["spoken_reply"]
