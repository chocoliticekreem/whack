"use client";

import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { ExternalLink, Maximize, RectangleHorizontal } from "lucide-react";
import { useChatCrowd } from "@/agents/chat-crowd/useChatCrowd";
import { normalizePlace } from "@/agents/chat-crowd/scene";
import { useSceneChat } from "@/agents/chat-crowd/useSceneChat";
import { AroundMe } from "@/components/around/AroundMe";
import { ChatDock } from "@/components/chat/ChatDock";
import { CopilotPanel, PANEL_W, RAIL_W, useCopilotOpen } from "@/components/copilot/CopilotPanel";
import { DevPanel } from "@/components/DevPanel";
import { TopBar } from "@/components/TopBar";
import { useFullscreen } from "@/components/useFullscreen";
import { MENU_ITEM, MoreMenu } from "@/components/MoreMenu";
import { PipStage, StageToolbarSlot } from "@/components/stream/PipStage";
import { Player } from "@/components/stream/Player";
import { StreamerBar } from "@/components/stream/StreamerBar";
import { prefetch } from "@/lib/client/cachedFetch";
import { LOCATIONS } from "@/lib/locations";
import type { StreamContext, StreamLocation, StreamSource } from "@/lib/types";
import { useSurroundings } from "@/platform/around/useSurroundings";
import { publish } from "@/platform/bus";
import { FeedPreview } from "@/components/stream/PhoneFeed";
import { usePhoneVideo } from "@/platform/live/rtc/usePhoneVideo";
import { useLiveLink } from "@/platform/live/useLiveLink";
import { sessionFromUrl } from "@/platform/live/useRemoteLive";
import { useWakeLock } from "@/platform/live/useWakeLock";

const BASE_VIEWERS = 1840;
// The 3D twin of the streamer's surroundings (/twin).
const MIRROR_URL = process.env.NEXT_PUBLIC_MIRROR_URL ?? "/twin";
const DESKTOP_QUERY = "(min-width: 768px)";

// Why: ChatPanel and the copilot own scroll/draft state, so it must be mounted exactly once —
// a CSS-only show/hide of two copies would fork that state.
function useIsDesktop() {
  const [desktop, setDesktop] = useState(() => window.matchMedia(DESKTOP_QUERY).matches);
  useEffect(() => {
    const mq = window.matchMedia(DESKTOP_QUERY);
    const onChange = () => setDesktop(mq.matches);
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, []);
  return desktop;
}

export function StreamShell() {
  const [source, setSource] = useState<StreamSource>({ kind: "idle" });
  const [demoLocation, setDemoLocation] = useState<StreamLocation>(LOCATIONS[0]);
  const [viewers, setViewers] = useState(0);
  const [revealedAt, setRevealedAt] = useState<number | null>(null);
  const [tab, setTab] = useState<View>("live");
  const [mirrorUrl, setMirrorUrl] = useState<string | null>(null);
  // Theatre: the player takes the full content width and the copilot folds to its rail.
  const [wide, setWide] = useState(false);
  const [camStream, setCamStream] = useState<MediaStream | null>(null);
  const [toolbarSlot, setToolbarSlot] = useState<HTMLDivElement | null>(null);
  const playerRef = useRef<HTMLDivElement>(null);
  const playerFs = useFullscreen(playerRef);
  const desktop = useIsDesktop();
  const [copilotOpen, setCopilotOpen] = useCopilotOpen(desktop);
  const live = source.kind !== "idle";
  const link = useLiveLink({ live, viewers, demo: demoLocation });
  const location = link.location;
  const session = useMemo(() => sessionFromUrl(), []);
  const phone = usePhoneVideo(session, link.followPhone);
  useWakeLock(live);
  useEffect(() => {
    // The 3D tab is an iframe of /twin on this origin, so warming the Cache API here makes its first paint instant.
    // The field is polled with no-store, so it is only fetched to get the server computing it early.
    const warm = () => {
      void prefetch("/api/twin/osm");
      void fetch("/api/twin/field").catch(() => {});
    };
    if ("requestIdleCallback" in window) {
      const id = window.requestIdleCallback(warm, { timeout: 5000 });
      return () => window.cancelIdleCallback(id);
    }
    const t = setTimeout(warm, 2000);
    return () => clearTimeout(t);
  }, []);

  const ctx: StreamContext = {
    streamer: "sonion",
    title: "IRL London walk — Tower Hill to Tower Bridge, pull up?",
    location,
    viewers: link.viewers,
    live,
    locationRevealedAt: revealedAt,
  };
  const { messages, send, inject } = useChatCrowd(ctx);
  useSceneChat(ctx, source, messages, inject);
  useSurroundings(location, live);

  useEffect(() => {
    if (!live) return;
    const id = setInterval(() => {
      setViewers((v) => {
        const target = BASE_VIEWERS * (revealedAt ? 2.6 : 1);
        const drift = (target - v) * 0.08 + (Math.random() - 0.45) * 40;
        return Math.max(1, Math.round(v + drift));
      });
    }, 1000);
    return () => clearInterval(id);
  }, [live, revealedAt]);

  useEffect(() => {
    if (live) publish({ type: "stream.viewers", viewers });
  }, [live, viewers]);

  // Stable identity: Player's webcam effect depends on it and would restart the camera.
  const changeSource = useCallback((next: StreamSource) => {
    setSource(next);
    if (next.kind === "idle") {
      setViewers(0);
      setRevealedAt(null);
    } else {
      setViewers((v) => (v === 0 ? Math.round(BASE_VIEWERS * 0.4) : v));
    }
  }, []);

  function changeLocation(next: StreamLocation) {
    setDemoLocation(next);
    if (source.kind === "url") {
      setSource({ kind: "url", url: next.camera.url, label: next.camera.label });
    }
    setRevealedAt(null);
    publish({ type: "stream.location", location: next });
  }

  function reveal() {
    const at = Date.now();
    setRevealedAt(at);
    publish({ type: "streamer:location_leaked", location, viewers, at });
    send(`yo I'm at ${normalizePlace(location.name, location.area).name} right now, come say hi`);
  }

  function mirror() {
    const url = new URL(MIRROR_URL, window.location.origin);
    url.searchParams.set("lat", String(location.lat));
    url.searchParams.set("lon", String(location.lon));
    url.searchParams.set("scenario", "leak_location_50");
    url.searchParams.set("location", location.id);
    url.searchParams.set("viewers", String(viewers));
    const session = new URLSearchParams(window.location.search).get("session");
    if (session) url.searchParams.set("session", session);
    setMirrorUrl(url.pathname + url.search);
    setTab("3d");
    publish({
      type: "reality:fork",
      scenario: "leak_location_50",
      location,
      viewers,
      label: `What if I tell ${viewers} viewers I'm at ${location.name}?`,
    });
  }

  const mainStyle = desktop ? { paddingRight: (copilotOpen ? PANEL_W : RAIL_W) + 24 } : undefined;

  const feedStream = source.kind === "webcam" ? camStream : source.kind === "idle" ? phone.stream : null;
  const clipUrl = source.kind === "url" || source.kind === "file" ? source.url : null;
  const feed =
    feedStream || clipUrl ? (
      <FeedPreview stream={feedStream} clipUrl={clipUrl} label={location.name} viewers={link.viewers} />
    ) : null;

  function toggleWide() {
    if (!wide) setCopilotOpen(false);
    setWide((w) => !w);
  }

  const chat = <ChatDock messages={messages} onSend={send} viewers={viewers} live={live} desktop={desktop} raised={!copilotOpen} />;
  const twinSrc = mirrorUrl ?? `/twin?lat=${location.lat}&lon=${location.lon}`;

  return (
    <div className="flex h-full flex-col">
      <TopBar live={live} />
      <DevPanel />
      <div className="relative flex min-h-0 flex-1">
        <main
          className={`scrollbar-thin flex min-w-0 flex-1 flex-col overflow-y-auto p-3 pb-24 transition-[padding] duration-200 sm:pb-24 md:pb-3 ${
            tab === "live" ? "gap-4 sm:p-4 md:pb-4 lg:gap-5 lg:p-6" : "gap-3"
          }`}
          style={mainStyle}
        >
          <div className="flex flex-wrap items-center justify-between gap-2 md:flex-nowrap md:justify-start">
            <ViewTabs tab={tab} onChange={setTab} />
            {desktop && chat}
            <div className="hidden shrink-0 items-center gap-2 md:ml-auto md:flex">
              <div ref={setToolbarSlot} className="contents" />
              {tab === "live" && (
                <button type="button" onClick={playerFs.toggle} className="pill glass">
                  <Maximize className="size-4" aria-hidden />
                  Full screen
                </button>
              )}
              {tab !== "around" && (
                <MoreMenu>
                  {(close) =>
                    tab === "live" ? (
                      <button
                        type="button"
                        role="menuitemcheckbox"
                        aria-checked={wide}
                        onClick={() => {
                          toggleWide();
                          close();
                        }}
                        className={MENU_ITEM}
                      >
                        <RectangleHorizontal className="size-4" aria-hidden />
                        Theatre mode
                        {wide && <span className="ml-auto size-2 rounded-full bg-[image:var(--grad-candy)]" />}
                      </button>
                    ) : (
                      <a href={twinSrc} target="_blank" rel="noopener" role="menuitem" onClick={close} className={MENU_ITEM}>
                        <ExternalLink className="size-4" aria-hidden />
                        Open 3D in new tab
                      </a>
                    )
                  }
                </MoreMenu>
              )}
            </div>
          </div>
          {/* The player stays mounted across tabs so the stream never drops. */}
          <div className={tab === "live" ? "flex flex-col gap-4 lg:gap-5" : "hidden"}>
            <div ref={playerRef}>
              <Player
                source={source}
                onSourceChange={changeSource}
                ctx={ctx}
                positionBadge={link.badge}
                sync={link.sync}
                remote={phone}
                size={playerFs.active ? "full" : wide ? "wide" : "default"}
                onCamStream={setCamStream}
              />
            </div>
            <StreamerBar
              ctx={ctx}
              positionSource={link.source}
              onLocationChange={changeLocation}
              onReveal={reveal}
              onSimulate={mirror}
            />
          </div>
          <StageToolbarSlot.Provider value={desktop ? toolbarSlot : null}>
            {tab === "around" && (
              <AroundMe location={location} activeCamUrl={source.kind === "url" ? source.url : null} feed={feed} />
            )}
            {tab === "3d" && <TwinView src={twinSrc} feed={feed} newTabInStage={!desktop} />}
          </StageToolbarSlot.Provider>
        </main>

        <CopilotPanel location={location} live={live} desktop={desktop} open={copilotOpen} onOpenChange={setCopilotOpen} />
        {!desktop && chat}
      </div>
    </div>
  );
}

type View = "live" | "around" | "3d";

const VIEWS: { id: View; label: string }[] = [
  { id: "live", label: "Live" },
  { id: "around", label: "Around me" },
  { id: "3d", label: "3D mirror" },
];

function ViewTabs({ tab, onChange }: { tab: View; onChange: (v: View) => void }) {
  return (
    <nav className="glass flex w-fit gap-1 rounded-full p-1.5" aria-label="Views">
      {VIEWS.map((v) => (
        <button
          key={v.id}
          type="button"
          onClick={() => onChange(v.id)}
          aria-current={tab === v.id ? "page" : undefined}
          className={`h-9 rounded-full px-4 text-[14px] font-extrabold tracking-[-0.01em] transition-[transform,background-color,color,box-shadow] duration-200 ease-[var(--ease-spring)] active:scale-95 ${
            tab === v.id
              ? "bg-white text-black shadow-[inset_0_2px_3px_#fff,inset_0_-3px_6px_rgb(236_17_135/0.2),0_6px_14px_-4px_rgb(0_0_0/0.5)]"
              : "text-white/60 hover:text-white"
          }`}
        >
          {v.label}
        </button>
      ))}
    </nav>
  );
}

// Why: the twin draws its view bar along the bottom edge and panels in both top corners.
const TWIN_BAR_CLEARANCE = 112;

function TwinView({ src, feed, newTabInStage }: { src: string; feed: ReactNode | null; newTabInStage: boolean }) {
  return (
    <PipStage
      className="min-h-[560px] flex-1"
      corner="bottom-right"
      size="S"
      bottomInset={TWIN_BAR_CLEARANCE}
      toolbarAlign="center"
      primaryLabel="3D mirror"
      secondaryLabel="stream"
      secondary={feed}
      primary={<iframe src={src} title="3D mirror" className="absolute inset-0 size-full border-0" allow="fullscreen; geolocation" />}
      toolbar={
        newTabInStage && (
          <a href={src} target="_blank" rel="noopener" className="pill pill-sm glass">
            <ExternalLink className="size-3.5" aria-hidden />
            New tab
          </a>
        )
      }
    />
  );
}
