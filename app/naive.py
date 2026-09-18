import os, time, logging
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from .db import connect, init_db, wait_for_db
from .models import ResetRequest, BuyRequest

logging.basicConfig(level="INFO")
log=logging.getLogger("naive")

app=FastAPI(title="Naive Seller (intentionally unsafe)")

@app.on_event("startup")
def startup():
    wait_for_db(); init_db()

@app.post("/reset")
def reset(req: ResetRequest):
    with connect() as conn:
        conn.execute("DELETE FROM tickets")
        conn.execute("DELETE FROM idempotency_keys")
        conn.execute("UPDATE sale_state SET total_tickets=%s,sold_count=0,sale_version=sale_version+1",(req.ticket_count,))
        conn.commit()
    return {"ok":True}

@app.post("/buy-naive")
def buy_naive(req: BuyRequest):
    # DO NOT USE: check-then-insert with no transaction/lock.
    with connect() as conn:
        existing=conn.execute("SELECT ticket_number,user_id FROM idempotency_keys WHERE request_id=%s",(req.request_id,)).fetchone()
        if existing:
            return {"status":"ok","ticket_number":existing["ticket_number"],"idempotent_replay":True}

        state=conn.execute("SELECT total_tickets,sold_count,sale_version FROM sale_state WHERE id=1").fetchone()
        if state["sold_count"] >= state["total_tickets"]:
            return JSONResponse(status_code=409,content={"status":"sold_out"})

        time.sleep(float(os.getenv("NAIVE_DELAY","0.005")))
        ticket_no=state["sold_count"]+1
        try:
            conn.execute("INSERT INTO tickets(ticket_number,user_id,request_id,sale_version) VALUES(%s,%s,%s,%s)",
                         (ticket_no,req.user_id,req.request_id,state["sale_version"]))
            conn.execute("INSERT INTO idempotency_keys(request_id,user_id,ticket_number,sale_version) VALUES(%s,%s,%s,%s)",
                         (req.request_id,req.user_id,ticket_no,state["sale_version"]))
            conn.execute("UPDATE sale_state SET sold_count=sold_count+1 WHERE id=1")
            conn.commit()
            return {"status":"ok","ticket_number":ticket_no,"idempotent_replay":False}
        except Exception as exc:
            conn.rollback()
            raise HTTPException(status_code=500,detail=str(exc))
