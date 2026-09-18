# ticket-stampede-complete-backend
Fifty thousand people want a hundred tickets, and they all arrive in the same sixty seconds.
# DYLA — Ticket Stampede

A concurrency-focused ticketing system built for the DYLA engineering challenge.

The problem is intentionally simple on the surface:

> **50,000 people arrive within 60 seconds and only 100 tickets exist.**

The interesting engineering problem is making the system remain correct when thousands of requests concurrently attempt to modify the same small inventory.

This submission builds **both sides of the system**:

* **Seller** — the ticket service responsible for correctness.
* **Buyer** — a configurable concurrent load client that attacks the seller, intentionally probes idempotency, measures performance, and verifies the final invariants.

It also includes an intentionally broken naive seller so the concurrency failure can be demonstrated rather than merely described.

---

# 1. What is included

```text
                     ┌──────────────────┐
                     │   Buyer / Load   │
                     │      Client      │
                     └────────┬─────────┘
                              │
                       concurrent requests
                              │
                              ▼
                     ┌─────────────────┐
                     │      Nginx      │
                     │  Load Balancer  │
                     └───────┬─────────┘
                             │
               ┌─────────────┼─────────────┐
               ▼             ▼             ▼
          ┌──────────┐  ┌──────────┐  ┌──────────┐
          │ Seller 1 │  │ Seller 2 │  │ Seller 3 │
          │ FastAPI  │  │ FastAPI  │  │ FastAPI  │
          └────┬─────┘  └────┬─────┘  └────┬─────┘
               │             │             │
               └─────────────┼─────────────┘
                             ▼
                    ┌──────────────────┐
                    │    PostgreSQL    │
                    │                  │
                    │ Sale state       │
                    │ Tickets          │
                    │ Idempotency      │
                    └──────────────────┘
```

### Components

| Component      | Purpose                                                     |
| -------------- | ----------------------------------------------------------- |
| FastAPI seller | Implements `/reset`, `/buy`, `/status`                      |
| PostgreSQL     | Durable source of truth                                     |
| Nginx          | Routes requests across three seller instances               |
| Buyer          | Generates concurrent traffic and validates invariants       |
| Naive seller   | Demonstrates the race condition in an unsafe implementation |
| Tests          | Automated correctness tests                                 |
| Experiments    | Benchmark and failure-injection results                     |
| `/logs`        | Full AI coding-session transcripts                          |
| `DECISIONS.md` | Engineering reasoning, trade-offs and limitations           |

---

# 2. The four invariants

Correctness comes before throughput.

The seller must preserve these invariants under concurrent load:

### Invariant 1 — No overselling

```text
successful purchases <= configured ticket inventory
```

If 100 tickets exist, there can never be 101 successful purchases.

### Invariant 2 — Ticket uniqueness

Every successful purchase receives a unique ticket number.

```text
unique(ticket_number) == successful purchases
```

### Invariant 3 — Idempotency

The same request ID can only create one purchase.

For example:

```text
POST /buy
user_id=alice
request_id=req-123
```

replayed multiple times must return the same ticket rather than create another one.

### Invariant 4 — Status consistency

The value reported by `/status` must agree with the tickets actually issued.

```text
sold_count == number of issued tickets
```

These invariants are verified by the buyer after the load test rather than being assumed from the implementation.

---

# 3. Why the fixed implementation is designed this way

The seller uses PostgreSQL as the shared source of truth.

I deliberately did **not** use a Python process-level mutex as the correctness mechanism.

That approach can make a single-process implementation appear correct, but it breaks down when requests are distributed across three independent seller instances:

```text
Seller 1 → Lock A
Seller 2 → Lock B
Seller 3 → Lock C
```

Those locks do not protect the same state.

Instead, the correctness boundary is the shared database.

A purchase is handled inside a database transaction:

```text
BEGIN
   │
   ├── lock sale state
   │
   ├── check request ID
   │
   ├── allocate available ticket
   │
   ├── persist ticket assignment
   │
   ├── persist idempotency record
   │
   ├── increment sold_count
   │
COMMIT
   │
   ▼
return successful response
```

The response is only successful after the transaction commits.

Database uniqueness constraints provide another layer of protection for ticket numbers and request IDs.

More detail about the architectural reasoning and rejected approaches is in:

**[`DECISIONS.md`](DECISIONS.md)**

---

# 4. The naive implementation

The repository intentionally contains a broken seller.

The naive implementation follows the unsafe pattern:

```text
check inventory
      ↓
choose ticket
      ↓
insert purchase
```

The problem is that concurrent requests can observe the same state before either request finishes updating it.

Conceptually:

```text
Request A                    Request B

check: ticket available      check: ticket available
       ↓                            ↓
choose ticket 42             choose ticket 42
       ↓                            ↓
insert 42                    insert 42
```

This is the race condition the fixed implementation is designed to eliminate.

The buyer/load tooling can attack the naive implementation so that the failure is demonstrated experimentally.

The naive service is intentionally unsafe and should never be exposed publicly.

---

# 5. Requirements

### Runtime

* Docker Desktop / Docker Engine
* Docker Compose
* Python 3.11+

The seller, PostgreSQL and Nginx run through Docker.

The buyer runs locally using Python.

---

# 6. Quick start — clean checkout

Clone the repository into a fresh directory.

Then:

```bash
docker compose up -d --build
```

Install the buyer dependencies:

```bash
python -m pip install -r requirements.txt
```

Verify the service:

```bash
curl http://localhost/health
```

Expected:

```text
{"status":"ok"}
```

Check the initial sale state:

```bash
curl http://localhost/status
```

---

# 7. Reset the sale

Create a fresh sale with 100 tickets:

```bash
curl -X POST http://localhost/reset \
  -H "content-type: application/json" \
  -d '{"ticket_count":100}'
```

The reset operation clears the previous sale state and initializes the new inventory.

---

# 8. Buy one ticket

```bash
curl -X POST http://localhost/buy \
  -H "content-type: application/json" \
  -d '{"user_id":"alice","request_id":"request-1"}'
```

A successful response contains the ticket number.

Example:

```json
{
  "ticket_number": 1
}
```

The exact ticket number depends on the current sale state.

---

# 9. Test idempotency manually

Send the same request twice:

```bash
curl -X POST http://localhost/buy \
  -H "content-type: application/json" \
  -d '{"user_id":"alice","request_id":"request-duplicate"}'
```

Repeat the exact request:

```bash
curl -X POST http://localhost/buy \
  -H "content-type: application/json" \
  -d '{"user_id":"alice","request_id":"request-duplicate"}'
```

Both successful responses should refer to the same ticket.

The second request must not consume another ticket.

A request ID reused with another user is rejected rather than silently transferring the purchase.

---

# 10. Check the final state

```bash
curl http://localhost/status
```

The response contains:

* configured inventory
* number sold
* issued tickets
* user/request ownership

The buyer independently reconciles this state after a load test.

---

# 11. Run the buyer

The buyer is the second half of the system.

It generates concurrent requests against the seller and measures:

* total requests
* successful purchases
* sold-out/conflict responses
* errors
* requests per second
* median response time
* p99 response time
* final inventory
* unique tickets
* idempotency behaviour
* all four invariants

Run the full challenge workload:

```bash
python loadtest/buyer.py \
  --base-url http://localhost \
  --requests 50000 \
  --concurrency 1000 \
  --duplicates 1000 \
  --tickets 100
```

On Windows PowerShell:

```powershell
.\scripts\run_buyer.ps1
```

The buyer finishes by producing an explicit:

```text
PASS
```

or:

```text
FAIL
```

for the invariant checks.

---

# 12. Example buyer output

The exact values below are illustrative; benchmark numbers in the submission are taken from actual runs and stored in `experiments/RESULTS.md`.

```text
============================================================
TICKET STAMPEDE LOAD TEST
============================================================

Configuration
  Requests:        50,000
  Concurrency:      1,000
  Tickets:             100
  Duplicate IDs:     1,000

------------------------------------------------------------
RESULTS
------------------------------------------------------------

Requests sent:          ...
Successful purchases:   ...
Sold-out/conflicts:     ...
Errors:                 ...

Requests/sec:           ...
Median latency:         ...
P99 latency:            ...

------------------------------------------------------------
INVARIANTS
------------------------------------------------------------

[PASS] Never sold more tickets than exist
[PASS] Never issued same ticket twice
[PASS] Duplicate request IDs produce one ticket
[PASS] Status count matches issued tickets

------------------------------------------------------------
OVERALL
------------------------------------------------------------

PASS
```

Actual benchmark output is not fabricated. See:

**[`experiments/RESULTS.md`](experiments/RESULTS.md)**

---

# 13. Run the naive failure experiment

The naive seller is exposed separately on port `8001`.

Start the naive service using the provided Docker setup/script.

Then:

```bash
python loadtest/probe_naive.py \
  --base-url http://localhost:8001 \
  --requests 1000
```

The purpose of this experiment is to demonstrate that the naive check-then-write implementation can be broken under concurrent access.

The actual failing output is recorded in:

**[`experiments/RESULTS.md`](experiments/RESULTS.md)**

I keep this implementation specifically because the failure is more useful evidence than simply stating that a race condition exists.

---

# 14. Three seller instances

The production/fixed configuration runs three independent seller containers:

```text
                Nginx
                  │
       ┌──────────┼──────────┐
       ▼          ▼          ▼
   seller-1   seller-2   seller-3
       │          │          │
       └──────────┼──────────┘
                  ▼
             PostgreSQL
```

There is intentionally no application-level lock shared between the seller processes.

This tests a more realistic condition:

```text
request A → seller 1
request B → seller 2
request C → seller 3
```

All three must still preserve the same four invariants.

The reason this works is that correctness is enforced through shared PostgreSQL state rather than process-local memory.

---

# 15. Why PostgreSQL is the correctness boundary

The system could have used an in-memory counter or a Python lock.

I rejected both.

### In-memory counter

Problems:

* state disappears when the process dies
* multiple instances have different counters
* durable idempotency becomes difficult
* the application becomes the source of truth

### Process-level lock

Problems:

* only protects one process
* does not coordinate three seller instances
* does not survive process failure
* does not provide durable state

### PostgreSQL

PostgreSQL provides:

* transactions
* row locking
* uniqueness constraints
* durable state
* shared state across seller instances

For this challenge, the database provides a much simpler correctness argument.

---

# 16. Failure behaviour

A successful purchase is not acknowledged until the database transaction commits.

If PostgreSQL becomes unavailable, the seller fails closed rather than claiming a ticket was sold without durable state.

This intentionally prioritizes:

```text
correctness > availability
```

for the purchase path.

There is also an important failure window:

```text
database COMMIT succeeds
        ↓
HTTP response is lost
        ↓
buyer retries
```

The retry uses the same request ID.

Because the idempotency record was committed with the purchase, the seller can return the existing ticket rather than allocating another one.

This is one reason idempotency is treated as persisted state rather than an in-memory cache.

---

# 17. Performance experiments

Correctness comes first.

Once the invariants pass, the buyer can be used to investigate performance.

I test increasing concurrency and record:

| Concurrency | Requests/sec | Median | P99 | Errors |
| ----------: | -----------: | -----: | --: | -----: |
|          10 |              |        |     |        |
|          50 |              |        |     |        |
|         100 |              |        |     |        |
|         250 |              |        |     |        |
|         500 |              |        |     |        |
|        1000 |              |        |     |        |

The purpose is not simply to find the largest RPS number.

I want to determine **where the bottleneck actually occurs**.

Possible bottlenecks include:

* buyer CPU/network
* seller CPU
* database contention
* database connections
* lock wait
* Nginx
* network latency

The measured results and interpretation are stored in:

**[`experiments/RESULTS.md`](experiments/RESULTS.md)**

---

# 18. Slow database experiment

The system also includes a slow-database experiment.

The scenario is:

```text
active sale
    ↓
database becomes slow
    ↓
buy requests continue arriving
    ↓
database recovers
```

The important property is not that requests remain fast.

The important property is that database slowness must not cause invalid successful sales.

The expected safety rule is:

> A ticket is not successfully acknowledged unless the database has durably recorded the purchase.

The actual experiment result is recorded in:

**[`experiments/RESULTS.md`](experiments/RESULTS.md)**

---

# 19. Database restart experiment

The database can also be stopped during an active sale and restarted.

This tests the application's behaviour when its state dependency disappears.

The test checks:

* overselling
* duplicate ticket numbers
* incorrect sold count
* false successful responses
* behaviour after recovery

This is not presented as production-grade database high availability.

The current system still has PostgreSQL as a dependency.

---

# 20. Testing

Automated tests cover core seller behaviour including:

* idempotent requests
* conflicting request IDs
* sold-out behaviour
* inventory correctness

Run:

```bash
docker compose exec db createdb -U postgres tickets_test
```

Then configure:

### Linux/macOS

```bash
export TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/tickets_test
pytest -q
```

### Windows PowerShell

```powershell
$env:TEST_DATABASE_URL="postgresql://postgres:postgres@localhost:5432/tickets_test"
pytest -q
```

The load client provides the higher-concurrency system-level verification.

---

# 21. Repository structure

```text
dyla-ticket-stampede/
│
├── app/
│   ├── main.py
│   ├── naive.py
│   ├── db.py
│   └── models.py
│
├── loadtest/
│   ├── buyer.py
│   └── probe_naive.py
│
├── tests/
│   └── test_invariants.py
│
├── experiments/
│   ├── RESULTS.md
│   └── slow_db.sql
│
├── nginx/
│   ├── nginx.conf
│   └── naive.conf
│
├── scripts/
│   ├── run_buyer.ps1
│   └── run_naive.ps1
│
├── logs/
│   ├── README.md
│   └── <actual AI session transcripts>
│
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── DECISIONS.md
├── README.md
└── .gitignore
```

---

# 22. AI-assisted development

AI coding tools were used during development.

The full coding-session transcripts are included under:

```text
/logs
```

I have kept the complete sessions rather than only the successful prompts.

The logs show:

* initial implementation
* debugging
* incorrect approaches
* architectural discussions
* changes made after testing
* cases where an AI suggestion was rejected or modified

The goal is to make the development process auditable rather than presenting AI-generated code as unexplained output.

---

# 23. Engineering decisions

The detailed reasoning is in:

**[`DECISIONS.md`](DECISIONS.md)**

It covers:

* architecture
* rejected approaches
* concurrency model
* idempotency
* multi-instance correctness
* trade-offs
* testing strategy
* measured results
* known limitations
* next steps

The most important design choice is that the system's correctness does not depend on a process-local lock.

---

# 24. Known limitations

This is intentionally not presented as a production-ready global ticketing platform.

Known limitations include:

### 1. Database dependency

PostgreSQL is the source of truth. If it is unavailable, the seller cannot safely acknowledge new purchases.

### 2. Serialization bottleneck

The current correctness strategy serializes the critical purchase path through shared database state.

This favors a simple correctness argument over maximum throughput.

### 3. Local load balancer

Nginx provides the three-instance experiment but is not a complete production HA/load-balancing architecture.

### 4. Small inventory assumption

The current inventory allocation strategy is deliberately simple because the challenge has only 100 tickets.

For very large inventories, I would use a dedicated inventory representation with indexed availability and more granular locking.

### 5. No fabricated fault-tolerance claims

The database restart and slow-database experiments are included only when actually executed and recorded.

I do not claim to have solved database high availability when the submission has not implemented it.

---

# 25. What I would investigate next

With another two weeks, I would focus on depth rather than adding unrelated features.

### Inventory scalability

Replace the single serialization point with a more scalable inventory allocation strategy and compare:

```text
current design
      vs
row-level inventory allocation
```

using measured contention and latency.

### Database resilience

Investigate:

* PostgreSQL replication
* failover
* connection loss
* transaction cancellation
* process crashes
* network partitions

### Observability

Add:

* structured logs
* database lock metrics
* request metrics
* distributed tracing
* seller/database resource correlation

### Distributed buyer

Move the load generator to multiple processes/machines and measure whether the buyer itself becomes the bottleneck.

The goal would be to prove where throughput is limited rather than assuming the seller is responsible.

---

# 26. Submission evidence

The repository is intended to contain the following evidence:

```text
README.md
    ↓
clean checkout instructions

DECISIONS.md
    ↓
why the system was designed this way

experiments/RESULTS.md
    ↓
actual naive failure + fixed results + performance/failure experiments

logs/
    ↓
complete AI coding sessions

code
    ↓
seller + buyer + tests + infrastructure
```

The benchmark results in `experiments/RESULTS.md` are generated from actual runs.

The AI session logs in `/logs` are the actual development transcripts.

No benchmark numbers or AI transcripts are fabricated.

---

# 27. Final clean-checkout checklist

Before submission:

```text
[ ] Fresh clone works
[ ] docker compose up -d --build works
[ ] /health works
[ ] /reset works
[ ] /buy works
[ ] /status works

[ ] Naive seller can be started
[ ] Naive race experiment executed
[ ] Actual naive failure recorded

[ ] Fixed seller passes concurrency test
[ ] 50,000-arrival test executed
[ ] Duplicate request IDs tested
[ ] All four invariants pass

[ ] Three seller instances tested
[ ] Nginx routing tested
[ ] Shared PostgreSQL correctness verified

[ ] Latency/concurrency measurements recorded
[ ] Bottleneck investigation recorded
[ ] Slow database experiment recorded if executed
[ ] Database restart experiment recorded if executed

[ ] DECISIONS.md completed
[ ] experiments/RESULTS.md completed
[ ] Full AI transcripts exported to /logs
[ ] No fabricated results
[ ] No local-only dependencies
[ ] README tested from a clean checkout
```

---

# 28. Submission

The repository should be private and shared with the DYLA/Thuli team as instructed in the challenge.

The submission email should include:

* GitHub repository
* LinkedIn profile
* short college/project background
* brief description of what was built
* any particularly important experiment or finding

The implementation is intentionally optimized around the core engineering question:

> **Can the system preserve correctness when concurrency is much larger than inventory?**

The buyer is therefore treated as an adversarial test client rather than merely a traffic generator, and the seller's correctness is established through database-backed invariants and experiments rather than assumptions.
