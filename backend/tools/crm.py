CUSTOMERS = {
    "C001": {"name":"Alice Johnson","tier":"Gold","total_flights":12,"open_issues":1},
    "C002": {"name":"Bob Smith","tier":"Silver","total_flights":3,"open_issues":0},
}

async def crm_lookup(args: dict, session_id: str) -> dict:
    cid = args.get("customer_id","")
    c = CUSTOMERS.get(cid)
    return {"status":"ok","customer":c} if c else {"status":"not_found","customer_id":cid}
