# whack

Our build for **WHACK 26** (Warwick, 7–8 Nov 2026) on the **Marshall Wace "New Worlds"** track.

**Status (Sun 8 Nov, ~00:50):** OverWatch is live at **https://overwatchcapital.vercel.app/multiverse**
(Vercel front end; the backend runs on our laptop behind Cloudflare tunnels). Merged through #87, no open PRs. Details
in [docs/build.md](docs/build.md) §0.

**What it is:**
- OverWatch is a live exchange on London's next five minutes. You play for points only; there is no real money.
- Every 5 minutes it lists plain-language city markets (which hub's crowd grows most, whether the Tube gets busier or
  quieter, whether the London VIX rises). Each is priced from 50 futures fitted to today's data and TfL's usual
  Saturday, reweighted by every reading. The central-London simulation stays out of the price (`SIM_WEIGHT=0`) until
  it beats persistence. Station markets settle on the first reading TfL stamped after betting closed.
- Click anywhere on the map to trade that area: the pin opens the market of the nearest real sensor ("Detected area:
  Waterloo · 440 m").
- Seven AI traders, each reading one live data feed, bet on the same markets as you. The leaderboard shows the top 10
  plus your own rank.
- **Exchange** (top bar, `/multiverse/exchange`): the same markets on a Kalshi-style order book with no house, traded
  by 2,196 AI accounts (12 market makers, 184 funds, 2,000 retail). Served by `services/city/venue_server.py` (:8062).
  The main screen still prices through the LMSR house.

## Quick start
```sh
scripts/run-data.sh &                                   # recorder + YOLO (:8047) + health (:8052)
MULTIVERSE_PORT=8349 python3 services/multiverse/server.py &   # Oxford Circus engine, source of the London VIX
python3 services/city/server.py &                       # city markets + wallet (:8051); polls the VIX on :8349
python3 services/city/venue_server.py &                 # the Exchange page's order books (:8062)
cd apps/stream && npx next dev -H 0.0.0.0 -p 3349               # open /multiverse
```
`run-data.sh` expects the YOLO server at `$YOLO_DIR`. It is Mirrorverse's `services/yolo`, which is not in this repo:
point `YOLO_DIR` at a [mirrorverse](https://github.com/chocoliticekreem/mirrorverse) checkout's `services/yolo` with
its `.venv` (the default is a path on Anson's laptop). For the public site, run
`cloudflared tunnel --url http://localhost:8051` and set Vercel's `CITY_URL` to the tunnel URL. `/` redirects to
`/multiverse`.

## Reused from Mirrorverse (disclosed, approved by the organisers)
Our earlier project [Mirrorverse](https://github.com/chocoliticekreem/mirrorverse) (built 17 Oct 2026). What still
runs from it:
- **The street-graph agent simulation** in `services/predict` (seed, personas, simulate, LLM wrapper). The Oxford
  Circus engine in `services/multiverse`, which produces the London VIX, runs 50 forked copies of it.
- **The YOLO camera counter** runs from the original Mirrorverse service, outside this repo (see `$YOLO_DIR` above).
- **The seeds** in `services/multiverse/seeds/` were built once from Mirrorverse's three.js twin: the Oxford Circus
  street field (`oxford-circus-field.json`) from its `/api/twin/field` route, and the central-London streets
  (`central-osm.json.gz`) from its cached OpenFreeMap tiles. They are committed, and both seed builders rebuild from
  them with `--offline`.

The Next.js app itself started as Mirrorverse's. All of its Mirrorverse code is removed: the streaming product
(persona chat, phone streaming, the "around me" map, the `/twin` page), the three.js twin and its API routes, and the
superseded `/multiverse/oxford` and `/multiverse/board` pages. `apps/stream` is now only the OverWatch pages,
`/multiverse` and `/multiverse/portfolio`.

## Docs, in reading order
| Doc | Read it for |
|---|---|
| [docs/idea.md](docs/idea.md) | **Start here.** The pitch, why we simulate as well as read data, the MW criteria, superseded ideas |
| [docs/build.md](docs/build.md) | What's shipped (by PR), what's in progress, ranked gaps, how to run it |
| [services/city/README.md](services/city/README.md) | The markets backend: series, pricing, integrity rules, wallet, endpoints |
| [docs/questions.md](docs/questions.md) | Why each market question was chosen, measured on Saturday's data |
| [docs/judge-prep.md](docs/judge-prep.md) | The hardest questions a Marshall Wace panel will ask, true answers, current numbers, the demo script |
| [docs/market-structure.md](docs/market-structure.md) | How the market works today: the LMSR house, how prices form, the AI traders, the points economy, integrity, and the economy simulation |
| [docs/exchange-design.md](docs/exchange-design.md) | Where it goes: a Kalshi-style order book with no house, where one player trades against thousands of AI market makers, hedge funds and retail |
| [docs/business.md](docs/business.md) | Fairness principle, market integrity, the path from game to exchange |
| [docs/marshall-wace.md](docs/marshall-wace.md) | The sponsor: brief, judging, who MW are, past winners |
| [docs/event.md](docs/event.md) | Logistics: rules, timetable, tracks and prizes, submission |
| [docs/data-sources.md](docs/data-sources.md) | Every data source researched: live vs history, auth, alternative data |
| [docs/design.md](docs/design.md) | An early 2D UI language (retro terminal). Superseded on `/multiverse` by the Kalshi × PostHog style (#38) |
| [docs/all-tracks-gpt.md](docs/all-tracks-gpt.md), [docs/all-tracks-gemini.md](docs/all-tracks-gemini.md) | James's AI-generated ideas for all four tracks (raw; takeaways in idea.md §3b) |
| [marketing/devpost.md](marketing/devpost.md) | The Devpost submission draft |
| [marketing/steering-log.md](marketing/steering-log.md) | Every human decision that redirected the build |
| [analysis/live_trading/README.md](analysis/live_trading/README.md) | The live bot trading pressure test and what it found |

## Key times
- **Sat 22:00:** doors lock (no re-entry without Discord).
- **Sun 09:00–11:00:** submit on Devpost. **11:00 is the hard deadline.**
- **Sun 12:30 onwards:** a strict 3-minute judging slot with MW.
- **Sun 17:30:** closing ceremony.
