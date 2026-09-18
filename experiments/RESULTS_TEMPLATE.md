# Experimental results

Do not invent these numbers. Paste raw terminal output from your own runs.

## Naive failure

Command:
```text
python loadtest/probe_naive.py --base-url http://localhost:8001 --requests 1000
```

Observed result:
- sold_count:
- ticket rows:
- duplicate ticket numbers:
- evidence of lost/incorrect state:

Paste the actual output here.

## Fixed 3-instance run

Command:
```text
python loadtest/buyer.py --base-url http://localhost --requests 50000 --concurrency 1000 --duplicates 1000 --tickets 100
```

Observed:
- RPS:
- p50:
- p99:
- invariant results:

Paste actual output here.

## Datastore slowdown / failure

Describe the exact experiment, timing, observed behavior, and recovery. Do not claim durability properties that were not demonstrated.
