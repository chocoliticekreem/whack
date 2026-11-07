> Reused from chocoliticekreem/mirrorverse (built 2026-10-17), approved by WHACK organisers.

# services/yolo

Local YOLO11n + ByteTrack over TfL JamCam clips (`/counts`, `/tracks`, `/frame`) and follower
detection for the streamer's own camera (`/follow/*`). Run with the project venv:

```sh
PORT=8047 python server.py
```

## Follower detection

`POST /follow/frame?session=<id>&t=<ms>[&lat=&lon=]` takes one JPEG from the streamer's camera
(the app sends one 640-px frame a second) and `GET /follow/state?session=<id>` returns the
per-session dossiers: how long each person has been visible, how many separate times they entered
the frame, how far the streamer has moved since first seeing them, and a `follower` flag.

A person is flagged when they have been visible for at least `FOLLOW_MIN_S` (90 s) and either
re-entered the frame `FOLLOW_MIN_APPEARANCES` (3) times or stayed while the streamer moved
`FOLLOW_MIN_MOVED_M` (150 m). All three are env overrides; the reasons for the defaults are next to
the constants in `server.py`.

### Privacy

- No face detection, no face embeddings, no identity lookup. People are matched between sightings
  by a colour histogram of their upper and lower body (clothes), so "same person" really means
  "same outfit". The app's copy says "possible follower" for that reason.
- Nothing is written to disk. Dossiers live in process memory for one streaming session, keyed by a
  random session id the browser generates, and are dropped after 10 minutes without a frame. A
  person not seen for 10 minutes is forgotten before that.
- Frames are processed and discarded. The only image kept is an optional 24x32 px torso thumbnail
  (top third of the box cropped away) so the streamer can tell which person the alert is about.
- Streamer coordinates are used only to compute distance moved and are not stored beyond the
  dossier's starting point for the session.
- The service binds to 127.0.0.1 only.
