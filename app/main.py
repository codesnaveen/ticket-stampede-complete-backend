import hashlib
import logging
import os
import time
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from psycopg.errors import UniqueViolation
from .db import connect, init_db, wait_for_db
from .models import ResetRequest, BuyRequest

logging.basicConfig(level=os.getenv("LOG_LEVEL","INFO"))
log=logging.getLogger("seller")

app=FastAPI(title="Ticket Stampede Seller", version="1.0")

@app.on_event("startup")
def startup():
    wait_for_db()
    init_db()

def advisory_key(request_id: str) -> int:
    # PostgreSQL advisory locks take a signed BIGINT.
    raw=hashlib.sha256(request_id.encode()).digest()[:8]
    n=int.from_bytes(raw, "big", signed=False)
    return n - (1<<64) if n >= (1<<63) else n

@app.post("/reset")
def reset(req: ResetRequest):
    started=time.perf_counter()
    with connect() as conn:
        with conn.transaction():
            # Reset is serialized against every buyer by locking the singleton state row.
            state=conn.execute(
                "SELECT sale_version FROM sale_state WHERE id=1 FOR UPDATE"
            ).fetchone()
            new_version=int(state["sale_version"])+1
            conn.execute("DELETE FROM tickets")
            conn.execute("DELETE FROM idempotency_keys")
            conn.execute("""
                UPDATE sale_state
                SET total_tickets=%s, sold_count=0, sale_version=%s, updated_at=now()
                WHERE id=1
            """,(req.ticket_count,new_version))
    return {"ok":True,"ticket_count":req.ticket_count,"sale_version":new_version,
            "latency_ms":round((time.perf_counter()-started)*1000,3)}

@app.post("/buy")
def buy(req: BuyRequest):
    started=time.perf_counter()
    try:
        with connect() as conn:
            with conn.transaction():
                # This lock makes reset and buy mutually exclusive.
                state=conn.execute("""
                    SELECT total_tickets,sold_count,sale_version
                    FROM sale_state WHERE id=1 FOR UPDATE
                """).fetchone()

                # Serialize identical request IDs across all seller instances.
                conn.execute("SELECT pg_advisory_xact_lock(%s)", (advisory_key(req.request_id),))

                idem=conn.execute("""
                    SELECT user_id,ticket_number,sale_version
                    FROM idempotency_keys WHERE request_id=%s
                """,(req.request_id,)).fetchone()
                if idem:
                    if idem["user_id"] != req.user_id:
                        raise HTTPException(
                            status_code=409,
                            detail="request_id already belongs to a different user"
                        )
                    return {
                        "status":"ok",
                        "ticket_number":idem["ticket_number"],
                        "request_id":req.request_id,
                        "idempotent_replay":True,
                        "latency_ms":round((time.perf_counter()-started)*1000,3),
                    }

                if state["sold_count"] >= state["total_tickets"]:
                    return JSONResponse(
                        status_code=409,
                        content={"status":"sold_out","message":"No tickets remain",
                                 "request_id":req.request_id}
                    )

                # The state row is already locked, so this ticket allocation is atomic.
                next_ticket=conn.execute("""
                    SELECT ticket_number
                    FROM generate_series(1,%s) AS ticket(ticket_number)
                    WHERE NOT EXISTS (
                        SELECT 1 FROM tickets t
                        WHERE t.ticket_number=ticket.ticket_number
                    )
                    ORDER BY ticket_number
                    LIMIT 1
                """,(state["total_tickets"],)).fetchone()
                if not next_ticket:
                    # Defensive consistency check: never claim a ticket if state is corrupt.
                    raise HTTPException(status_code=500, detail="inventory/state mismatch")

                ticket_no=next_ticket["ticket_number"]
                conn.execute("""
                    INSERT INTO tickets(ticket_number,user_id,request_id,sale_version)
                    VALUES(%s,%s,%s,%s)
                """,(ticket_no,req.user_id,req.request_id,state["sale_version"]))
                conn.execute("""
                    INSERT INTO idempotency_keys(request_id,user_id,ticket_number,sale_version)
                    VALUES(%s,%s,%s,%s)
                """,(req.request_id,req.user_id,ticket_no,state["sale_version"]))
                conn.execute("""
                    UPDATE sale_state
                    SET sold_count=sold_count+1, updated_at=now()
                    WHERE id=1
                """)
                return {
                    "status":"ok","ticket_number":ticket_no,
                    "request_id":req.request_id,"idempotent_replay":False,
                    "latency_ms":round((time.perf_counter()-started)*1000,3),
                }
    except HTTPException:
        raise
    except Exception as exc:
        log.exception("buy failed")
        # Fail closed: a DB/network error cannot be converted into a successful sale.
        raise HTTPException(status_code=503, detail="seller temporarily unavailable") from exc

@app.get("/status")
def status():
    with connect() as conn:
        with conn.transaction():
            state=conn.execute("""
                SELECT total_tickets,sold_count,sale_version
                FROM sale_state WHERE id=1
            """).fetchone()
            rows=conn.execute("""
                SELECT ticket_number,user_id,request_id
                FROM tickets ORDER BY ticket_number
            """).fetchall()
    return {
        "total_tickets":state["total_tickets"],
        "sold_count":state["sold_count"],
        "sale_version":state["sale_version"],
        "tickets":[dict(r) for r in rows],
    }

@app.get("/health")
def health():
    try:
        with connect() as conn:
            conn.execute("SELECT 1")
        return {"ok":True}
    except Exception:
        return JSONResponse(status_code=503, content={"ok":False})
