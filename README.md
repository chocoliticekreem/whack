# whack

Our build for **WHACK 26** (Warwick, 7–8 Nov 2026) on the **Marshall Wace "New Worlds"** track.

**Status (Sat 7 Nov, evening):** OverWatch is live at **https://overwatchcapital.vercel.app/multiverse**
(Vercel front end; the markets backend runs on our laptop behind a Cloudflare tunnel). Merged through #46. Details in
[docs/build.md](docs/build.md) §0.

**What it is:**
- OverWatch is a live exchange on London's next five minutes. You play for points only; there is no real money.
- Every 5 minutes it lists city-wide markets (which hub surges, City Pulse over/under, the London VIX). Each one is
  priced from 50 futures: statistical walks fitted to today's data, with a share (half by default, `SIM_WEIGHT`) drawn
  from the central-London agent simulation when it's running. Real TfL data narrows them and settles the market.
- You trade against seven AI traders, and each of them reads one live data feed.

## Quick start
```sh
scripts/run-data.sh &                                   # recorder + YOLO (:8047) + health (:8052)
MULTIVERSE_PORT=8349 python3 services/multiverse/server.py &   # Oxford Circus engine, source of the London VIX
python3 services/city/server.py &                       # city markets + wallet (:8051); polls the VIX on :8349
cd apps/stream && MULTIVERSE_URL=http://127.0.0.1:8349 npx next dev -H 0.0.0.0 -p 3349   # open /multiverse
```
`run-data.sh` expects the YOLO server at `$YOLO_DIR` (it defaults to a path on Anson's laptop; point it at
`services/yolo` with its `.venv`). For the public site, run `cloudflared tunnel --url http://localhost:8051` and set
Vercel's `CITY_URL` to the tunnel URL.

## Docs, in reading order
| Doc | Read it for |
|---|---|
| [docs/idea.md](docs/idea.md) | **Start here.** The pitch, why we simulate as well as read data, the MW criteria, superseded ideas |
| [docs/build.md](docs/build.md) | What's shipped (by PR), what's in progress, ranked gaps, how to run it |
| [services/city/README.md](services/city/README.md) | The markets backend: series, pricing, integrity rules, wallet, endpoints |
| [docs/questions.md](docs/questions.md) | Why each market question was chosen, measured on Saturday's data |
| [docs/business.md](docs/business.md) | Fairness principle, market integrity, the path from game to exchange |
| [docs/marshall-wace.md](docs/marshall-wace.md) | The sponsor: brief, judging, who MW are, past winners |
| [docs/event.md](docs/event.md) | Logistics: rules, timetable, tracks and prizes, submission |
| [docs/data-sources.md](docs/data-sources.md) | Every data source researched: live vs history, auth, alternative data |
| [docs/design.md](docs/design.md) | An early 2D UI language (retro terminal). Superseded on `/multiverse` by the Kalshi × PostHog style (#38) |
| [docs/all-tracks-gpt.md](docs/all-tracks-gpt.md), [docs/all-tracks-gemini.md](docs/all-tracks-gemini.md) | James's AI-generated ideas for all four tracks (raw; takeaways in idea.md §3b) |
| [marketing/devpost.md](marketing/devpost.md) | The Devpost submission draft |

## Key times
- **Sat 22:00:** doors lock (no re-entry without Discord).
- **Sun 09:00–11:00:** submit on Devpost. **11:00 is the hard deadline.**
- **Sun 12:30 onwards:** a strict 3-minute judging slot with MW.
- **Sun 17:30:** closing ceremony.
