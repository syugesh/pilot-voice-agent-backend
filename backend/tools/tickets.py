import uuid, logging
logger = logging.getLogger("pilot.tools.tickets")


async def ticket_create(args: dict, session_id: str) -> dict:
    from db.engine import AsyncSessionLocal
    from db.models import Ticket
    ref = f"TKT-{str(uuid.uuid4())[:6].upper()}"
    async with AsyncSessionLocal() as db:
        db.add(Ticket(
            ticket_ref=ref, session_id=session_id,
            category=args.get("category", "general"),
            synopsis=args.get("synopsis", ""),
            symptoms=args.get("symptoms", ""),
            priority=args.get("priority", "normal"),
            escalated=bool(args.get("escalated", False)),
            escalation_target=args.get("escalation_target"),
        ))
        await db.commit()
    logger.info(f"Ticket created: {ref} (priority={args.get('priority','normal')}, escalated={bool(args.get('escalated'))})")
    return {"status": "ok", "ticket_ref": ref,
            "escalated": bool(args.get("escalated", False)),
            "escalation_target": args.get("escalation_target")}


async def _find_ticket(db, args: dict, session_id: str):
    """Resolve a ticket by explicit ref if given, else the most recent open
    ticket for this session (so 'close this ticket' on a live call works)."""
    from db.models import Ticket
    from sqlalchemy import select, desc
    ref = args.get("ticket_id") or args.get("id") or args.get("ticket_ref")
    if ref:
        return (await db.execute(select(Ticket).where(Ticket.ticket_ref == ref))).scalar_one_or_none()
    return (await db.execute(
        select(Ticket).where(Ticket.session_id == session_id, Ticket.status != "closed")
        .order_by(desc(Ticket.id)).limit(1)
    )).scalar_one_or_none()


async def ticket_update(args: dict, session_id: str) -> dict:
    from db.engine import AsyncSessionLocal
    async with AsyncSessionLocal() as db:
        ticket = await _find_ticket(db, args, session_id)
        if not ticket:
            return {"status": "not_found", "message": "No matching ticket to update."}
        for field in ("status", "priority", "synopsis", "symptoms", "category", "escalation_target"):
            if args.get(field) is not None:
                setattr(ticket, field, args[field])
        if args.get("escalated") is not None:
            ticket.escalated = bool(args["escalated"])
        if args.get("note"):
            ticket.symptoms = ((ticket.symptoms or "") + "\n" + args["note"]).strip()
        await db.commit()
        logger.info(f"Ticket updated: {ticket.ticket_ref}")
        return {"status": "ok", "ticket_ref": ticket.ticket_ref, "ticket_status": ticket.status}


async def ticket_close(args: dict, session_id: str) -> dict:
    from db.engine import AsyncSessionLocal
    async with AsyncSessionLocal() as db:
        ticket = await _find_ticket(db, args, session_id)
        if not ticket:
            return {"status": "not_found", "message": "No matching ticket to close."}
        ticket.status = "closed"
        if args.get("resolution"):
            ticket.resolution = args["resolution"]
        await db.commit()
        logger.info(f"Ticket closed: {ticket.ticket_ref}")
        return {"status": "ok", "ticket_ref": ticket.ticket_ref,
                "closed": ticket.ticket_ref, "resolution": ticket.resolution or ""}
