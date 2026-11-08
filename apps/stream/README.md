# OverWatch web app

The Next.js front end for OverWatch. `/` redirects to `/multiverse`.

## Run

```bash
npm ci
npm run dev        # http://localhost:3000, lands on /multiverse
```

The pages proxy to the Python backends; set these in `apps/stream/.env.local` when they are not on the defaults:

- `CITY_URL`: the city markets and wallet (`services/city`, default `http://127.0.0.1:8051`). `/api/city/*`.
- `SIM_URL`: the central-London simulation (`services/multiverse`, default `http://127.0.0.1:8449`). `/api/sim/*`.

The London VIX comes from the Oxford Circus engine (`services/multiverse` on :8349), which the city service polls
directly; the web app never calls it.

## Pages

| Route | What |
|---|---|
| `/multiverse` | OverWatch: the city markets, map, traders and "What if…" |
| `/multiverse/portfolio` | Your bets, positions, P&L and ledger |

## Reused from Mirrorverse

> Reused from chocoliticekreem/mirrorverse (built 2026-10-17), approved by WHACK organisers.

The app started as Mirrorverse's Next.js app. All of its Mirrorverse code is removed: the streaming product, the
three.js London twin and its API routes, and the superseded `/multiverse/oxford` and `/multiverse/board` pages. What
remains is OverWatch's own code.

Powered by TfL Open Data.
