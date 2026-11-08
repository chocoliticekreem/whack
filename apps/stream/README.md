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
- `MULTIVERSE_URL`: the Oxford Circus engine (`services/multiverse`). `/api/multiverse/*`, used by
  `/multiverse/board` and `/multiverse/oxford`.

## Pages

| Route | What |
|---|---|
| `/multiverse` | OverWatch: the city markets, map, traders and "What if…" |
| `/multiverse/portfolio` | Your bets, positions, P&L and ledger |
| `/multiverse/board` | Superseded: the Exchange of Worlds board for Oxford Circus |
| `/multiverse/oxford` | Superseded: "Which London is real?" on the three.js twin |

## Reused from Mirrorverse

> Reused from chocoliticekreem/mirrorverse (built 2026-10-17), approved by WHACK organisers.

The three.js London twin (`src/twin`, with `src/platform`, `src/lib` and `src/components`) and its routes
(`/api/around/*`, `/api/twin/*`, `/api/predict`, `/api/live/stream`) come from Mirrorverse. Only `/multiverse/oxford`
uses them, and `services/multiverse/real_seed.py` calls them when it rebuilds the Oxford Circus seed. Those routes read
`TFL_APP_KEY`, `JAMCAM_YOLO_URL`, `PREDICT_URL` and, for the twin's camera search, the optional `WANDB_*` keys.
`/multiverse` and `/multiverse/portfolio` use none of this code.

Powered by TfL Open Data.
