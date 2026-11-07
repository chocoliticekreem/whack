> Reused from chocoliticekreem/mirrorverse (built 2026-10-17), approved by WHACK organisers.

# Mirrorverse

An IRL streaming platform that gives London streamers eyes on the city around them.
It also links to a parallel 3D London, where they can simulate what happens before they tell chat where they are.

## Run

```bash
npm install (repo root)
npm run dev -w stream
```

Open http://localhost:3000.

## Chat and Weights & Biases

Chat is a crowd of personas (superfan, opportunist, hater, local, questioner, mod). `POST /api/chat/scene` batches a few persona lines per call on W&B Inference and reacts to the video frame (when one can be read), the streamer's messages, the location leak ("Tell chat my location") and the Mirror. Replies are strict JSON, validated (length, language, real names, distances, repeats) and every call has a timeout. Each batch is traced in Weave as `chat.persona_batch` with `chat.scene_describe` and `chat.write_lines` under it.

Without a W&B key, or when a call fails, the same personas speak from local template lines. `useChatBackend()` (`platform/chat/crowd.ts`) reports `"wandb"` or `"local"`.

Put these in `apps/stream/.env.local` (names only here, see `.env.example`):

- `WANDB_API_KEY`: turns the model on. Without it the route returns 501 and chat stays local.
- `WANDB_TEAM`, `WANDB_PROJECT`: the Weave project (`team/project`). Without them there are no traces.
- `WANDB_MODEL`, `WANDB_VISION_MODEL`, `WANDB_BASE_URL`: optional overrides of the text model, the vision model and the inference URL.

In development, `POST /api/chat/scene?mock=1` answers from the local lines through the same validation (`?mock=dirty` adds lines the validator must drop), so the route can be tried without a key.

## Structure

The code is laid out by CAMP layer (Coding agents, Agent runtimes, Model services, Platform). See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the layers and the cross-tab event bus the 3D city integrates through. Team to-dos are in [TODO.md](TODO.md).

## Sponsor tools

- **YOLO**: crowd counts on TfL JamCams
- **NVIDIA Cosmos**: reasoning about incidents near the streamer
- **Weights & Biases**: inference for personas and the advisor agent, with Weave traces
- **VAST AI OS**: clip ingest and semantic search
- **CoreWeave**: model serving
- **Cursor**: dev environment

Powered by TfL Open Data.

## Streaming from your phone

Camera and GPS need HTTPS on a phone, so the dev server is exposed through a Cloudflare quick tunnel.

1. Laptop: `npm run dev:stream` (port 3047), then in a second terminal `npm run tunnel` (set `PORT=3051 npm run tunnel` for another port). Copy the `https://<random>.trycloudflare.com` URL it prints. The host is already in `allowedDevOrigins` in `next.config.ts`.
2. Phone: open that URL, tap **Go live** (rear camera + GPS) and allow camera and location. Tap the flip icon for the selfie camera. The screen stays awake while live. The badge under the clock shows `Phone GPS · ±8 m · 2 s ago` and `Sync: connected`.
3. Laptop: open `http://localhost:3047` and `http://localhost:3047/twin`. Both follow the phone: the Live tab and Around me take the phone's position (`Following phone`), the twin's beacon, heading cone and **Streamer view** move with it. More than 650 m from Tower Hill the twin says `Outside the mirrored area`; Around me works anywhere.

The phone posts its state to `POST /api/live/state` every 2 s; every other page listens on `GET /api/live/stream` (server-sent events). State is in memory, one session called `default`; add `?session=<name>` to the phone and laptop URLs to run several streamers on one server. Without GPS the location picker is the **Demo location**.

### Phone video on the laptop (WebRTC)

While the laptop is `Following phone`, its Live tab shows the phone's camera over WebRTC instead of the idle card (chip: `Live from phone · 0.4 s`, the one-way delay estimate from RTT/2 + jitter buffer), and the 3D mirror tab shows it picture-in-picture. Signalling goes through this server only: the laptop posts `join` to `POST /api/live/signal`, the phone (listening on `GET /api/live/signal/stream?role=phone`) opens one `RTCPeerConnection` per viewer and sends the offer; answers and ICE candidates go back the same way. Video only, capped at 720p / 24 fps / 1.5 Mbps. The laptop re-joins every 5 s until the phone answers and reconnects on drop; after 20 s without a connection it shows `Phone video unavailable (state only)` and keeps trying.

ICE uses Google's public STUN (`stun:stun.l.google.com:19302`), no TURN. Phone and laptop on the same Wi-Fi, or a phone on 4G/5G with the laptop on an ordinary home/office network, usually connect directly. Symmetric NATs on both ends (some corporate or venue Wi-Fi, some carrier-grade NAT) need a TURN relay, which is not provided: the state relay (position, heading) still works, only the video falls back.

Set `NEXT_PUBLIC_MIRROR_URL` in your local env file in `apps/stream` to change where **Mirror** opens the city (default `/twin`, the 3D twin in this app).
