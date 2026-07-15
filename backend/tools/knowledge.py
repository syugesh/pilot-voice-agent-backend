KB = [
    {
        "id": 1,
        "title": "Password Reset",
        "content": "Visit /forgot-password. OTP sent to email. Expires in 10 minutes.",
    },
    {
        "id": 2,
        "title": "Flight Booking Policy",
        "content": "Flights can be cancelled within 24 hours for a full refund.",
    },
    {
        "id": 3,
        "title": "Baggage Allowance",
        "content": "Economy: 23kg checked, 7kg cabin. Business: 32kg checked, 7kg cabin.",
    },
    {
        "id": 4,
        "title": "Check-in",
        "content": "Online check-in opens 48 hours before departure. Closes 1 hour before.",
    },
    {
        "id": 5,
        "title": "Upgrades",
        "content": "Upgrades available at check-in subject to availability and fare class.",
    },
]


async def kb_search(args: dict, session_id: str) -> dict:
    q = args.get("query", "").lower()
    results = [
        {"id": d["id"], "title": d["title"], "excerpt": d["content"][:100]}
        for d in KB
        if any(w in d["content"].lower() or w in d["title"].lower() for w in q.split())
    ]
    return {"status": "ok", "results": results[:3]}
