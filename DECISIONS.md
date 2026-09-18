# Decisions

## Architecture
The seller is a small FastAPI service behind Nginx, with three independent application instances sharing PostgreSQL. PostgreSQL is the source of truth for inventory, issued tickets, and idempotency records.

I rejected an in-process mutex because it cannot protect a sale when requests are routed to different processes/containers. I also rejected an in-memory counter because it cannot survive process failure and does not provide durable idempotency.

The correctness path uses a singleton `sale_state` row as the serialization point for buy/reset state changes. Each buy transaction locks that row, checks persisted idempotency, allocates the lowest available ticket, inserts the ticket and idempotency record, and increments `sold_count` before commit. A PostgreSQL transaction-scoped advisory lock additionally serializes the same request ID across instances.

## Trade-offs
This intentionally favors correctness over maximum throughput. Locking one row means buy transactions serialize, which is a known bottleneck. The brief says correctness comes first; the buyer benchmark is used to measure the cost instead of assuming it is acceptable.

The ticket lookup currently uses a simple `generate_series` query. For a 100-ticket sale this is transparent and adequate. At much larger inventory sizes I would use a dedicated inventory table with row-level locking and an indexed available state.

The service fails closed when PostgreSQL is unavailable: it returns 503 instead of acknowledging a sale. A confirmed response is sent only after the database transaction commits.

## Testing
The automated tests cover idempotency, conflicting request IDs, and sold-out behavior. The buyer adds concurrency, duplicate replay, status reconciliation, and latency/RPS measurement. The naive service exists only to demonstrate the race that the fixed implementation removes.

Real benchmark output belongs in `experiments/RESULTS.md`; numbers must come from an actual run.

## Known limitations
- The seller depends on PostgreSQL availability; there is no independent replicated database in this submission.
- The singleton row serializes all buys, limiting throughput.
- Nginx is a simple local load balancer, not a production HA setup.
- The naive experiment is intentionally unsafe and must never be exposed publicly.
- A crash after a committed transaction but before the HTTP response can cause the client to retry; persisted request IDs make that retry return the original ticket.

## Next two weeks
I would replace the singleton lock with a scalable inventory allocation scheme, benchmark multiple database configurations, add database replication/failover, add structured metrics/tracing, and run fault-injection tests for connection loss, transaction cancellation, process crashes, and network partitions. I would also make the load generator multi-process and compare client CPU/network utilization against seller/database utilization.
