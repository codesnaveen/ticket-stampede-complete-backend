
-- Optional fault-injection helper for a live PostgreSQL session.
-- Run manually while the seller is under load.
-- This deliberately holds the sale row lock for 10 seconds:
BEGIN;
SELECT * FROM sale_state WHERE id=1 FOR UPDATE;
SELECT pg_sleep(10);
COMMIT;

-- Expected property: concurrent buys wait and then either commit correctly
-- or return an error; they must never receive a false success.
