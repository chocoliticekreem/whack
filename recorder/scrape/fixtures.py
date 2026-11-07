"""London home football fixtures today/tomorrow, from TheSportsDB's public API (free key "123").

Kick-off and egress (~kick-off + 115 min) send crowds through specific stations, and fans route
through central interchanges. One record per run: data/raw/scrape_fixtures/<day>/<hour>.ndjson.gz.
"""

import json
from datetime import datetime, timedelta, timezone

from scrape.cache import cached_get

API = "https://www.thesportsdb.com/api/v1/json/123"
EGRESS_AFTER_MIN = 115  # 90 min play + 15 half-time + ~10 stoppage

# TheSportsDB idTeam -> club, stadium, lat, lon, stations fans use (2026-27 Premier League + Championship).
LONDON_CLUBS = {
    "133604": ("Arsenal", "Emirates Stadium", 51.5549, -0.1084,
               ["Arsenal", "Holloway Road", "Finsbury Park", "Highbury & Islington"]),
    "133610": ("Chelsea", "Stamford Bridge", 51.4817, -0.1910, ["Fulham Broadway", "West Brompton"]),
    "133616": ("Tottenham Hotspur", "Tottenham Hotspur Stadium", 51.6043, -0.0664,
               ["White Hart Lane", "Seven Sisters", "Tottenham Hale"]),
    "133600": ("Fulham", "Craven Cottage", 51.4749, -0.2217, ["Putney Bridge", "Hammersmith"]),
    "133632": ("Crystal Palace", "Selhurst Park", 51.3983, -0.0855, ["Selhurst", "Norwood Junction", "Thornton Heath"]),
    "134355": ("Brentford", "Gtech Community Stadium", 51.4907, -0.2886, ["Brentford", "Kew Bridge", "Gunnersbury"]),
    "133636": ("West Ham United", "London Stadium", 51.5387, -0.0166,
               ["Stratford", "Hackney Wick", "Pudding Mill Lane"]),
    "133851": ("Charlton Athletic", "The Valley", 51.4865, 0.0364, ["Charlton"]),
}


def parse_events(events: list[dict], today, days: int = 2) -> list[dict]:
    """TheSportsDB events -> London home fixtures kicking off on `today` .. today+days-1 (UTC dates)."""
    out = {}
    last_day = today + timedelta(days=days - 1)
    for ev in events or []:
        home_id = ev.get("idHomeTeam")
        if home_id not in LONDON_CLUBS or not ev.get("strTimestamp"):
            continue
        kickoff = datetime.fromisoformat(ev["strTimestamp"]).replace(tzinfo=timezone.utc)
        if not today <= kickoff.date() <= last_day:
            continue
        club, stadium, lat, lon, stations = LONDON_CLUBS[home_id]
        out[ev["idEvent"]] = {
            "idEvent": ev["idEvent"], "league": ev.get("strLeague"), "home": ev.get("strHomeTeam"),
            "away": ev.get("strAwayTeam"), "kickoff_utc": kickoff.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "egress_est_utc": (kickoff + timedelta(minutes=EGRESS_AFTER_MIN)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "venue": stadium, "lat": lat, "lon": lon, "stations": stations, "status": ev.get("strStatus"),
            "home_score": ev.get("intHomeScore"), "away_score": ev.get("intAwayScore"),
        }
    return sorted(out.values(), key=lambda r: r["kickoff_utc"])


def scrape(now: datetime, log) -> list[dict]:
    """eventsnext + eventslast per club: next covers upcoming, last covers today's finished match."""
    events, failed = [], 0
    for team_id in LONDON_CLUBS:
        for endpoint, key in (("eventsnext", "events"), ("eventslast", "results")):
            try:
                events += json.loads(cached_get(f"{API}/{endpoint}.php?id={team_id}")).get(key) or []
            except Exception as e:  # noqa: BLE001 - one club failing must not drop the others
                failed += 1
                log.error(f"fixtures {endpoint} {team_id}: {type(e).__name__}: {e}")
    if failed == 2 * len(LONDON_CLUBS):
        raise RuntimeError("every TheSportsDB call failed")
    return parse_events(events, now.date())
