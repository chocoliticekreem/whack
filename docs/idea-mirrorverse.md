---
title: "Idea: Mirrorverse — London's what-if engine"
date: 2026-11-07
status: ideating (code reuse approved, not pulled yet)
track: Marshall Wace — New Worlds (see marshall-wace-track.md)
prior_art: https://github.com/chocoliticekreem/mirrorverse (built 2026-10-17, VAST hackathon)
---

> **Latest thinking is in [idea-shortlist.md](idea-shortlist.md)** (the exchange of worlds /
> reality prunes the multiverse). This doc is the engine and reuse map.

# Mirrorverse: a mirror of London where you can stage events that never happened

> **One-liner:** Drop a car crash on Oxford Street and watch the city react: traffic jams up,
> buses divert, crowds spill, ambulances route in. It's a mirror world of London, run forward
> from live data.

## Why it fits "New Worlds"
- **"A door to the unexplored":** the one world nobody can see is the one that *didn't* happen.
  Mirrorverse makes counterfactuals visible.
- **"New data":** the output is data that doesn't exist yet: a forecast of an event that never
  occurred, seeded from real live feeds (TfL cameras, buses, disruptions, weather).
- **The MW angle (say this in the pitch):** MW's day job is scenario analysis, asking "what
  happens to the book if X shocks?" We stress-test a *city* the way they stress-test a portfolio.
  It also hits last year's MW prompt, "map data between domains to uncover new insights":
  traffic → transit → crowds → emergency response.

**Closest prior winner:** MW's IC Hack 26 "Emergent Behaviour" prize went to
[Digital Twin](https://devpost.com/software/eventtwin), a multi-agent simulation of an event's
crowd. That validates the direction, so we must clearly go further: a real city, live data,
several systems, a visible cascade. See [past-winners.md](past-winners.md).

## ⚠ Track choice: we must pick ONE sponsor
From the opening ceremony: one problem statement per team, judged only by that sponsor in a
3-minute slot. Mirrorverse touches three of the four:
| Track | Fit | Prize |
|---|---|---|
| **Marshall Wace, New Worlds** | The multiverse / "parallel present" framing. The novelty bar is high | iPad + Pencil + merch (per member? unconfirmed) |
| **incident.io, live-feed agent with personality** | **Near-literal match for the October build**: live TfL cameras, an agent reacting in the moment, the persona chat crowd | Portable monitor, **each member** |
| Brevan Howard, an AI researcher that tells a real discovery from a lucky one | Only the backtest/credibility angle | Apple Watch, each member |

Decide this deliberately, not by default. MW gives the most creative room. incident.io is the
easiest fit with the code we're allowed to reuse.

## ⚠ Steering check: is it *novel*, or just on theme?
This year's brief asks for "something novel no one has seen before" (see the brief comparison
in marshall-wace-track.md). An honest read:
- **The risk:** a literal "crash → traffic cascade" sim is almost exactly MW's **2024** brief
  ("a game where your actions have consequences"). It's also a known category: digital twins,
  SimCity, and IC Hack 26's winning Digital Twin. Pitched as "a city traffic simulator", it
  reads as *last year's theme* and *seen before*.
- **What's actually new:** the **counterfactual present**. This is not a model of a city, it's
  *a parallel version of today's real London*, forked from live data at this minute. A world
  that exists only because you changed one thing. Nobody has walked through the present that
  didn't happen.

**Sharpen toward "New Worlds" (pick one or two, don't do all):**
1. **Branching multiverse:** every intervention forks a new world, and the worlds form a tree.
   Users explore *a map of possible Londons*, compare branches, and see where they diverge
   most. The "world" is the product.
2. **New data:** each fork produces a dataset that has never existed, such as "what Oxford
   Street's traffic would look like right now if…". Show it next to the real feed, as a new
   data source.
3. **Explore, don't just watch:** drop into the counterfactual world in first person and walk
   the street that only exists in this branch.

**Pitch words:** "parallel present", "the London that didn't happen", "fork reality".
**Avoid:** "digital twin", "traffic simulator", "chain of events".

**Ask MW at the stand:** "Is a counterfactual fork of the real city, live, novel enough for
New Worlds, or does it feel like a sim?" Their answer decides how hard we push the multiverse
angle.

## How it scores on MW's 3 criteria
| Criterion | What makes it land |
|---|---|
| **Creativity** | Counterfactual worlds you author by clicking. Nobody else's "new world" will be a live mirror of a real city |
| **Technical complexity** | Live data ingest, a real OSM road graph, agent-based sim with re-routing, cascade propagation, 3D rendering. All of it is explainable in 30 s |
| **Good presentation** | The demo *is* the pitch: click, crash, watch the ripple spread. Show a split screen of reality vs. the mirror |

## The core loop (the demo)
1. **Mirror:** a 3D twin of a real London area (OSM buildings and roads) is filled with live
   state: TfL JamCam people/vehicle counts, live bus positions and arrivals, road disruptions and
   weather.
2. **Intervene:** the user drops an event on the map: a **car crash on Oxford Street**, a road
   closure, a tube station shut, a concert letting out, a downpour.
3. **Cascade:** the sim plays forward (for example 30 minutes in 60 s) and the effects ripple
   outward:
   - **Traffic:** capacity drops at the crash and vehicles re-route on the road graph, so queues
     grow on parallel streets.
   - **Buses:** routes through the closure divert, and arrivals downstream slip.
   - **Pedestrians:** rubberneckers gather, pavements jam, people shift to the nearest tube.
   - **Emergency:** an ambulance routes from the nearest station through the jam and its ETA is
     shown.
4. **Read out:** a report gives the top 3–5 changes with time, place, size and confidence, plus a
   **cascade graph** (crash → closure → bus 73 diverted → Bond St crowding +40%).
5. **Compare:** reality and the mirror side by side, with a timeline scrubber.

**The 10-second "door" moment:** the user clicks Oxford Street and a red pulse spreads street by
street across the West End.

## What already exists in the mirrorverse repo (reuse map)
| Need | Already built | Where |
|---|---|---|
| 3D London twin from OSM | Yes, but centred on **Tower Hill** | `apps/stream/src/twin/` (`origin.ts`, `osm.ts`, `network/`) |
| Live feeds | TfL JamCams, buses, disruptions, weather, air, crime | `apps/stream/src/app/api/around/*` |
| People/vehicle counts | YOLO11 + ByteTrack on JamCams | `services/yolo/server.py` |
| Live "field" (counts per street segment) | Yes | `apps/stream/src/twin/field/`, `/api/twin/field` |
| **What-if engine** | **Yes:** MiroFish-style persona groups on the real street graph, Dijkstra re-planning. Interventions are `closure`, `incident`, `event` and `weather` | `services/predict` (`POST /predict` with `interventions`) |
| Ghost/forecast layer in 3D | Yes | `apps/stream/src/twin/predict/` (`Ghost.tsx`, `Panel.tsx`, `ReplayHud.tsx`) |
| Emergency vehicles, traffic sim | Partial | `twin/live/Emergency.tsx`, `twin/live/sim/traffic.ts` |
| Scoring a forecast vs reality | Yes | `services/predict` `/score` |

**What's new for WHACK:**
1. **Click-to-intervene UX:** place an event anywhere and choose its type and severity.
2. **Cascade layer:** propagate across systems (roads → buses → pedestrians → emergency), not
   just pedestrians, and draw the causal chain as a graph.
3. **Move the origin to Oxford Street / the West End**, or make the origin selectable.
4. **Reality vs mirror split-screen** with a timeline scrubber.
5. Drop the IRL-streamer-safety framing. The pitch is "a mirror world for what-ifs".

## Reusing last month's code: approved
Mirrorverse was built at another hackathon (17 Oct 2026). **The organisers confirmed on
Sat 7 Nov that we can build on it.** The plan:
- Fork it and **say so openly in the submission**. Pitch and get judged on the new layer:
  intervene, cascade, West End, split screen.
- **Don't pull the code yet. We're still ideating.** Lock the idea and scope first, then bring in
  only the pieces in the reuse map above.

## Scope for 24 hours (Sat 11:00 → Sun 11:00)
**Must have (the demo):**
- West End twin (OSM roads + buildings) around Oxford Circus.
- Live TfL layer: at least buses + disruptions + a few JamCams.
- Click to drop "car crash" → road capacity cut → vehicles re-route → visible jam spreads.
- Buses diverted, and a cascade readout listing the 3–5 biggest changes.

**Should have:**
- Pedestrian spill-over and tube station crowding.
- Ambulance ETA through the jam.
- More event types: closure, station shut, concert, rain.
- An LLM narrator that writes the "what happened" report.

**Stretch:**
- Score the mirror against reality: replay a *real* past incident from TfL disruption history
  and compare.
- Chain events ("crash + rain + match day").

## Data sources (all public, no auth or a free key)
- **TfL Unified API:** JamCams (`/Place/Type/JamCam`), road disruptions (`/Road/all/Disruption`),
  bus arrivals and routes (`/Line/{id}/Route/Sequence`, `/StopPoint/.../Arrivals`), line status,
  station crowding (`/crowding/{naptan}`).
- **OpenStreetMap / Overpass:** roads with lanes and speed limits, buildings, ambulance stations,
  hospitals.
- **Open-Meteo:** weather.

## Pitch spine (2 min, draft)
1. "Every city runs one timeline. We built a second one." Show the mirror of the West End, live.
2. "What if a car crashed on Oxford Street, right now?" Click. The pulse spreads.
3. Walk the cascade: jam → bus 73 diverts → Bond St crowds → ambulance ETA +6 min.
4. "How": live TfL feeds → road graph → agent sim → cascade graph. One sentence each.
5. "Marshall Wace stress-tests portfolios. We stress-test a city." Close on the split screen.

## Open questions
- [x] **Organiser: can we build on code from a previous hackathon?** Yes (confirmed Sat 7 Nov). Disclose it in the submission.
- [ ] MW stand: does a city-sim reading of "New Worlds" land with them? Pitch it at the 14:00 talk.
- [ ] Team: who owns twin/UX, sim/cascade, data, pitch?
- [ ] West End only, or a selectable origin?
