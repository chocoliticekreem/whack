"""Public venue table for the fixture rule in events.py.

Covers every venue the recorder's fixtures scraper can emit (recorder/scrape/fixtures.py
LONDON_CLUBS: London Premier League + Championship home grounds, 2026-27). A venue that is not
here is still handled when the fixture row carries lat/lon (events.py uses DEFAULT_CAPACITY).

Sources:
- capacity: each stadium's Wikipedia infobox (seated football capacity), read 2025. Rounded to the
  infobox figure; real attendance varies by a few thousand.
- lat/lon and nearest stations: the same values the recorder scraper uses (TheSportsDB venue
  location, cross-checked against OpenStreetMap), so the fixture feed and this table agree.
"""

VENUES = {
    "Emirates Stadium": {"lat": 51.5549, "lon": -0.1084, "capacity": 60704, "sport": "football",
                         "stations": ["Arsenal", "Holloway Road", "Finsbury Park", "Highbury & Islington"]},
    "Stamford Bridge": {"lat": 51.4817, "lon": -0.1910, "capacity": 40173, "sport": "football",
                        "stations": ["Fulham Broadway", "West Brompton"]},
    "Tottenham Hotspur Stadium": {"lat": 51.6043, "lon": -0.0664, "capacity": 62850, "sport": "football",
                                  "stations": ["White Hart Lane", "Seven Sisters", "Tottenham Hale"]},
    "Craven Cottage": {"lat": 51.4749, "lon": -0.2217, "capacity": 29600, "sport": "football",
                       "stations": ["Putney Bridge", "Hammersmith"]},
    "Selhurst Park": {"lat": 51.3983, "lon": -0.0855, "capacity": 25486, "sport": "football",
                      "stations": ["Selhurst", "Norwood Junction", "Thornton Heath"]},
    "Gtech Community Stadium": {"lat": 51.4907, "lon": -0.2886, "capacity": 17250, "sport": "football",
                                "stations": ["Brentford", "Kew Bridge", "Gunnersbury"]},
    "London Stadium": {"lat": 51.5387, "lon": -0.0166, "capacity": 62500, "sport": "football",
                       "stations": ["Stratford", "Hackney Wick", "Pudding Mill Lane"]},
    "The Valley": {"lat": 51.4865, "lon": 0.0364, "capacity": 27111, "sport": "football",
                   "stations": ["Charlton"]},
}

# Typical gate-to-final-whistle minutes by sport (football: 90 + 15 half-time + ~10 stoppage,
# the scraper's EGRESS_AFTER_MIN; the others are standard match lengths).
DURATION_MIN = {"football": 115, "rugby": 100, "cricket": 210, "tennis": 150, "american_football": 200,
                "concert": 180}

# A fixture at a venue missing from VENUES but with a location: assume a mid-size ground.
DEFAULT_CAPACITY = 20000
