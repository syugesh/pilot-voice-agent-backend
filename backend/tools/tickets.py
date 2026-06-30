import uuid, logging
logger = logging.getLogger("pilot.tools.tickets")

async def ticket_create(args: dict, session_id: str) -> dict:
    from db.engine import AsyncSessionLocal
    from db.models import Ticket
    ref = f"TKT-{str(uuid.uuid4())[:6].upper()}"
    async with AsyncSessionLocal() as db:
        db.add(Ticket(
            ticket_ref=ref, session_id=session_id,
            category=args.get("category","general"),
            synopsis=args.get("synopsis",""),
            symptoms=args.get("symptoms",""),
        ))
        await db.commit()
    logger.info(f"Ticket created: {ref}")
    return {"status": "ok", "ticket_ref": ref}

async def ticket_update(args: dict, session_id: str) -> dict:
    return {"status": "ok", "updated": args.get("field")}

async def ticket_close(args: dict, session_id: str) -> dict:
    return {"status": "ok", "closed": args.get("id"), "resolution": args.get("resolution","")}
