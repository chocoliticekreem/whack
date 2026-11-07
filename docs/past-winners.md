---
title: Past winners — Marshall Wace tracks + WHACK 2025
date: 2026-11-07
sources:
  - https://ic-hack-26.devpost.com/project-gallery
  - https://durhack-2025.devpost.com/project-gallery
  - https://durhack-2024.devpost.com/project-gallery
  - https://whack-2025.devpost.com/project-gallery
---

# Past winners and why they won

Award names come from each Devpost gallery's structured data. "What it does" comes from the
project pages where they had one.

## 1. Marshall Wace track winners (the judges we face)

| Event | MW prize | Winner | What it is | Built with | Team |
|---|---|---|---|---|---|
| **IC Hack 26** (Feb 2026) | **Emergent Behaviour** (iPad + Pencil) | [**Digital Twin** (EventTwin)](https://devpost.com/software/eventtwin) | Builds an AI twin of you from onboarding questions. It mingles with twins of real people in a **multi-agent simulation of an event** (up to 7 Claude agents with different personalities), then predicts whether you'll enjoy it: go or skip | Claude, LangGraph, LangChain, React, SQLite + vector embeddings, FourSquare/Eventbrite APIs | 3 |
| IC Hack 26 | Emergent Behaviour (honourable mention) | [**ERLA**](https://devpost.com/software/magi-2bhmze) | A recursive research agent: a **fleet of parallel "Scout" agents** search Semantic Scholar, summarise and generate hypotheses. A master agent spawns and prunes scouts, and every summary passes a pure-Python HaluGate check (≥95% groundedness) | Anthropic, OpenAI, FastAPI, torch/transformers, LettuceDetect, pytest | 2 |
| **DurHack 2025** (Nov 2025) | **Bridging Worlds** (iPads + stash) | [**ORION**](https://devpost.com/software/orion-efom8k) | A 3D crystal orb that makes predictions by comparing your data with "similar" data it knows, "some comparisons may get quite wild!" | Blender, Svelte, FastAPI, Python | 4 |
| **DurHack 2024** | **Chain of events** | [**Pipeline of Peril**](https://devpost.com/software/pipeline-of-peril) | A Rube Goldberg machine for data. One message travels through Gmail → Morse code servo → Discord bots → QR codes → Google Keep → Chrome extension → Minecraft blocks → WiFi → **real traffic lights read by OpenCV**. It also won Hackiest Hack | ESP32, Arduino, OpenCV, Java, Python, websockets, Minecraft, real traffic lights | 4 |
| DurHack 2024 | **Funniest Hack** | [**None of Your Business**](https://devpost.com/software/none-of-your-business) | A game where young people try out managerial roles and business processes. It also won Level Up Equality | Godot | 4 |

## 2. Patterns: why these won with MW

1. **Two of the last three MW awards went to multi-agent systems** (Digital Twin, ERLA). The
   prize names give the taste away: "Emergent Behaviour" and "Chain of events". MW reward
   **systems where many parts interact and something surprising emerges**, and they want to *see*
   it happen.
2. **Simulating people is a proven winner.** Digital Twin won by simulating a crowd. **This is
   the closest prior art to Mirrorverse.** It validates the direction but raises the bar: we must
   clearly go further (see section 4).
3. **Playful framing over a real technical core.** A crystal orb, a Rube Goldberg email client, a
   business game. Every winner has a hook you remember in five seconds, plus genuine engineering
   underneath (OpenCV on dimming bulbs, a hallucination gate, multi-agent orchestration).
   Creativity, technical complexity and presentation all show up in each one.
4. **Mapping data across domains.** ORION compares your data with unrelated data. Pipeline of
   Peril pushes one message through ten unrelated systems. This matches MW's 2025 prompt to
   "map data between domains to uncover new insights".
5. **Predictions and forecasts recur.** Digital Twin predicts enjoyment and ORION predicts
   outcomes. MW is a prediction business.
6. **Breadth of tech that is visibly integrated.** The winners list 5–15 technologies, all doing
   real work in the demo. Pipeline of Peril went wide on purpose ("touch as many technologies as
   possible").
7. **Full teams (3–4) and one strong demo,** not a feature list.
8. **Winners often win twice.** Pipeline of Peril (Chain of events + Hackiest) and None of Your
   Business (Funniest + Level Up Equality) also won another category. Projects can be submitted
   to several prizes, so enter every category we fit.
9. **What did *not* win the MW category:** literal readings of the theme. At DurHack 2025, four
   "meet in the middle" apps went after a co-sponsor's prompt, and the generic "bridge" apps
   (skill swap, web3 health data) won nothing from MW.

## 3. WHACK 2025 (this event last year, MW were not a sponsor)

| Prize | Winner | What it is |
|---|---|---|
| Capital One Title | [Snackonomics](https://devpost.com/software/snackonomics) | Snack and habit tracker |
| Brevan Howard Title | [Whackyyy](https://devpost.com/software/whackyyy-information-accuracy-dashboard) | Dashboard that uses LLMs to check how accurate systems are |
| incident.io Title | [CharacterCompass](https://devpost.com/software/charactercompass) | Real-time AI character-profile generator for authors |
| **Most Creative Hack** | [WHACKMOPOLY](https://devpost.com/software/whackmopoly) | "Monopoly if it was made after the 2008 financial crisis" |
| WHACKiest Hack | [Finance Clicker](https://devpost.com/software/finance-clicker) | A clicker game about finance |
| Best Solo Hack | [PINN-acle](https://devpost.com/software/isaac-mortiboy) | Physics-informed neural nets for arbitrage-free local-vol surfaces + sentiment, explained by an LLM |
| 1 Hour Hackathon | [SRE agent](https://devpost.com/software/knowledge-base-assissted-sre-conversational-agent) | Knowledge-base-assisted incident agent |
| Best Costume | [Kachow](https://devpost.com/software/kachow) | News sentiment analysis |

**WHACK-specific patterns:**
- Judges here like **finance-flavoured humour**: WHACKMOPOLY, Finance Clicker.
- There are general prizes on top of the sponsor ones: **Most Creative, WHACKiest, Best Solo,
  1 Hour Hackathon (Sat 22:30) and Best Costume.** These are extra chances to win.
- The sponsor title winners were simple, well-executed tools with an AI component, not huge
  systems.

## 4. What this means for Mirrorverse
- **Lean into emergent behaviour.** The cascade *is* emergent behaviour: one crash, and
  thousands of agents' decisions add up to a jam nobody planned. Use MW's own words in the pitch.
- **Differentiate from Digital Twin (IC Hack 26).** They simulated a *social* crowd at one event
  with 7 LLM agents and made-up twins. We simulate a **real city**:
  - seeded from live TfL data;
  - thousands of agents across several systems (roads, buses, pedestrians, ambulances);
  - the cascade visible in 3D;
  - optionally scored against what actually happened.
  Mention it if asked. Don't hide it.
- **Add one playful hook**, the way their winners do. Ideas: a "chaos button" (random disasters),
  "replay real history" (pick a past TfL incident and rerun it with one change), or a
  sliding-doors split screen.
- **Make the forecast a prediction**, with a number, a confidence and the drivers behind it.
  That's MW's language.
- **Submit to several prizes**: MW New Worlds + Most Creative + WHACKiest, if the rules allow.
