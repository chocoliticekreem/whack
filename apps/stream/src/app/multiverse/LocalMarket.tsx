"use client";

import "./local.css";
import type { Marker } from "maplibre-gl";
import { MapPin, X } from "lucide-react";
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { getCityMap, onMapPick } from "./CityMapGL";
import { isLandscapePhone } from "./Live";
import { londonClock } from "./city-store";
import type { Market } from "./city-types";

/** The sensor a local market settles on (services/city/local.py). */
export interface LocalSensor {
  kind: "cam" | "station";
  id: string;
  name: string;
  lat: number;
  lon: number;
}

export type LocalMarketJson = Market & { local: { sensor: LocalSensor; distanceM?: number } };

interface ForecastOnly {
  lat: number;
  lon: number;
  label: string;
  reason: string;
  note: string | null;
  nearest?: { name: string; kind: string; lat: number; lon: number; distanceM: number };
  forecast: { at: number; horizonMin: number; people: number; segments: { id: string; people: number; distanceM: number }[] } | null;
}

type View =
  | { kind: "loading" }
  | { kind: "market"; id: string; sensor: LocalSensor; distanceM?: number }
  | { kind: "forecast"; data: ForecastOnly }
  | { kind: "error"; text: string };

const LIST_MS = 5000;
const CARD_W = 340;
const GAP = 18;

const isPhone = () => window.matchMedia("(max-width: 720px)").matches || isLandscapePhone();

function pinElement(className: string, label: string): HTMLDivElement {
  const el = document.createElement("div");
  el.className = className;
  el.setAttribute("aria-label", label);
  el.innerHTML = '<span class="lm-drop"></span><span class="lm-shadow"></span>';
  return el;
}

function sensorWord(s: LocalSensor) {
  return s.kind === "cam" ? "JamCam" : "Tube station";
}

/** Click anywhere on the map: a magenta pin drops there and a compact card opens on the nearest real sensor's
 * market for this round, or on the simulation's street forecast when no sensor can settle a bet there. */
export function LocalMarket({
  mock,
  player,
  token,
  disabled,
  error,
  marketOf,
  onMarket,
  card,
}: {
  mock: boolean;
  player: string;
  token: string | null;
  /** True while another map mode owns clicks (a "What if" scenario), so a click never turns into a trade. */
  disabled?: boolean;
  /** The last bet error, shown in the card too: on a phone the sheet covers the market list. */
  error?: string | null;
  marketOf: (id: string) => Market | undefined;
  onMarket: (m: Market) => void;
  card: (m: Market) => ReactNode;
}) {
  const [at, setAt] = useState<{ lat: number; lon: number } | null>(null);
  const [view, setView] = useState<View | null>(null);
  const [, setMoved] = useState(0);
  const [open, setOpen] = useState<LocalMarketJson[]>([]);
  const lib = useRef<typeof import("maplibre-gl") | null>(null);
  const pin = useRef<Marker | null>(null);
  const spots = useRef(new Map<string, Marker>());
  const seq = useRef(0);
  const off = useRef(disabled);
  useEffect(() => {
    off.current = disabled;
  }, [disabled]);

  useEffect(() => {
    void import("maplibre-gl").then((m) => (lib.current = m));
  }, []);

  const close = useCallback(() => {
    setView(null);
    setAt(null);
    pin.current?.remove();
    pin.current = null;
  }, []);

  const dropPin = useCallback((lat: number, lon: number) => {
    const map = getCityMap();
    if (!map || !lib.current) return;
    pin.current?.remove();
    pin.current = new lib.current.Marker({ element: pinElement("lm-pin", "Your pin"), anchor: "bottom" })
      .setLngLat([lon, lat])
      .addTo(map);
    setAt({ lat, lon });
  }, []);

  const pick = useCallback(
    async (lat: number, lon: number) => {
      dropPin(lat, lon);
      const mine = ++seq.current;
      if (mock) {
        setView({ kind: "error", text: "Local markets need the live exchange; demo data has none." });
        return;
      }
      if (!token) {
        setView({ kind: "error", text: "Setting up your player. Try again in a moment." });
        return;
      }
      setView({ kind: "loading" });
      try {
        const res = await fetch("/api/city/local", {
          method: "POST",
          headers: { "content-type": "application/json", "x-player-token": token },
          body: JSON.stringify({ player, lat, lon }),
          cache: "no-store",
        });
        const data = (await res.json()) as { market?: LocalMarketJson; forecastOnly?: ForecastOnly; error?: string };
        if (mine !== seq.current) return;
        if (data.market) {
          onMarket(data.market);
          setView({ kind: "market", id: data.market.id, sensor: data.market.local.sensor, distanceM: data.market.local.distanceM });
        } else if (data.forecastOnly) setView({ kind: "forecast", data: data.forecastOnly });
        else setView({ kind: "error", text: data.error ?? `The exchange said no (HTTP ${res.status})` });
      } catch {
        if (mine === seq.current) setView({ kind: "error", text: "Could not reach the exchange" });
      }
    },
    [dropPin, mock, onMarket, player, token],
  );

  useEffect(() => {
    if (disabled) return;
    onMapPick(({ lat, lon }) => void pick(lat, lon));
    return () => onMapPick(null);
  }, [disabled, pick]);

  useEffect(() => {
    if (!disabled) return;
    pin.current?.remove();
    pin.current = null;
  }, [disabled]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && close();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [close]);

  // Every open local market this round shows as a small pin on its sensor; a click opens it.
  useEffect(() => {
    if (mock) return;
    let live = true;
    const load = () =>
      fetch("/api/city/local", { cache: "no-store" })
        .then((r) => (r.ok ? r.json() : null))
        .then((d: { markets: LocalMarketJson[] } | null) => live && d && setOpen(d.markets))
        .catch(() => undefined);
    void load();
    const id = window.setInterval(load, LIST_MS);
    return () => {
      live = false;
      window.clearInterval(id);
    };
  }, [mock]);

  useEffect(() => {
    const map = getCityMap();
    if (!map || !lib.current) return;
    const ids = new Set(open.map((m) => m.id));
    for (const [id, mk] of spots.current) {
      if (!ids.has(id)) {
        mk.remove();
        spots.current.delete(id);
      }
    }
    for (const m of open) {
      if (spots.current.has(m.id)) continue;
      const s = m.local.sensor;
      const el = pinElement("lm-spot", `${s.name}: ${m.question}`);
      el.setAttribute("role", "button");
      el.title = m.question;
      el.addEventListener("click", (e) => {
        e.stopPropagation();
        if (off.current) return;
        dropPin(s.lat, s.lon);
        onMarket(m);
        setView({ kind: "market", id: m.id, sensor: s, distanceM: 0 });
      });
      spots.current.set(m.id, new lib.current.Marker({ element: el, anchor: "bottom" }).setLngLat([s.lon, s.lat]).addTo(map));
    }
  }, [open, dropPin, onMarket]);

  useEffect(() => {
    const current = spots.current;
    return () => {
      current.forEach((m) => m.remove());
      current.clear();
      pin.current?.remove();
    };
  }, []);

  // The card follows its pin as the map moves.
  useEffect(() => {
    const map = getCityMap();
    if (!map || !at) return;
    const moved = () => setMoved((n) => n + 1);
    map.on("move", moved);
    return () => {
      map.off("move", moved);
    };
  }, [at]);

  const map = getCityMap();
  if (disabled || !view || !at || !map) return null;
  const xy = map.project([at.lon, at.lat]);

  const phone = isPhone();
  const style = phone
    ? undefined
    : {
        left: Math.max(GAP, Math.min(window.innerWidth - CARD_W - GAP, xy.x + GAP)),
        top: Math.max(64, Math.min(window.innerHeight - 380, xy.y - 120)),
      };
  const m = view.kind === "market" ? marketOf(view.id) : undefined;

  return (
    <div className="lm-card" style={style} role="dialog" aria-label="Local market" onClick={(e) => e.stopPropagation()}>
      <div className="lm-head">
        <MapPin size={14} strokeWidth={2.5} aria-hidden />
        <span className="lm-head-text">
          {view.kind === "market" && (
            <>
              {sensorWord(view.sensor)} <b>{view.sensor.name}</b>
              {view.distanceM ? <span className="tl-muted"> · {view.distanceM} m away</span> : null}
            </>
          )}
          {view.kind === "forecast" && <b>{view.data.label}</b>}
          {view.kind === "loading" && "Finding the nearest live sensor…"}
          {view.kind === "error" && "Local market"}
        </span>
        <button type="button" className="tl-stake-x" aria-label="Close" onClick={close}>
          <X size={16} />
        </button>
      </div>
      {view.kind === "market" && error && <p className="lm-body lm-error">{error}</p>}
      {view.kind === "market" && (m ? card(m) : <div className="tl-market tl-empty">Opening the market…</div>)}
      {view.kind === "forecast" && <Forecast f={view.data} onTrade={(lat, lon) => void pick(lat, lon)} />}
      {view.kind === "error" && <p className="lm-body">{view.text}</p>}
    </div>
  );
}

function Forecast({ f, onTrade }: { f: ForecastOnly; onTrade: (lat: number, lon: number) => void }) {
  const fc = f.forecast;
  return (
    <div className="lm-body lm-forecast">
      {fc ? (
        <>
          <span className="tl-big tl-num">{Math.round(fc.people)}</span>
          <span className="lm-forecast-what">
            people forecast on the {fc.segments.length} nearest {fc.segments.length === 1 ? "street" : "streets"} at{" "}
            <span className="tl-data">{londonClock(fc.at)}</span>
          </span>
          <span className="tl-muted">
            Nearest street {fc.segments[0].distanceM} m away. From the central-London simulation, 50 worlds on the street graph.
          </span>
        </>
      ) : (
        <span className="tl-muted">{f.note ?? "No forecast for this spot."}</span>
      )}
      {f.nearest ? (
        <button type="button" className="lm-nearest" onClick={() => onTrade(f.nearest!.lat, f.nearest!.lon)}>
          Trade {f.nearest.name} instead · {f.nearest.distanceM.toLocaleString("en-GB")} m
        </button>
      ) : (
        <span className="lm-why">Nothing here can settle a bet, so there is nothing to trade.</span>
      )}
      <footer className="tl-src">
        <span className="tl-src-line">OverWatch central simulation, forecast only, not tradeable</span>
      </footer>
    </div>
  );
}
