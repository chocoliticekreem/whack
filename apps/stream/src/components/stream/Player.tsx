"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { Camera, Film, RotateCcw, Square, SwitchCamera, Upload } from "lucide-react";
import type { StreamContext, StreamSource } from "@/lib/types";
import { usePhonePublisher } from "@/platform/live/rtc/usePhonePublisher";
import type { PhoneVideo } from "@/platform/live/rtc/usePhoneVideo";
import { sessionFromUrl } from "@/platform/live/useRemoteLive";
import { PhoneFeed } from "./PhoneFeed";

interface PlayerProps {
  source: StreamSource;
  onSourceChange: (s: StreamSource) => void;
  ctx: StreamContext;
  /** e.g. "Phone GPS · ±8 m · 2 s ago" */
  positionBadge?: string;
  /** e.g. "Sync: connected" */
  sync?: string;
  /** The phone's WebRTC video while this page follows a phone; shown in place of the idle card. */
  remote?: PhoneVideo;
  /** "wide" fills the content width; "full" fills a fullscreen parent. */
  size?: "default" | "wide" | "full";
  /** Hands the webcam stream to the shell so the map and twin views can show it as a PiP. */
  onCamStream?: (s: MediaStream | null) => void;
}

type Facing = "environment" | "user";

function useClock() {
  const [now, setNow] = useState<Date | null>(null);
  useEffect(() => {
    const tick = () => setNow(new Date());
    const first = setTimeout(tick, 0);
    const id = setInterval(tick, 1000);
    return () => {
      clearTimeout(first);
      clearInterval(id);
    };
  }, []);
  return now;
}

function formatViewers(n: number) {
  return n >= 1000 ? `${(n / 1000).toFixed(1)}K` : String(n);
}

function coords(lat: number, lon: number) {
  return `${lat.toFixed(4)}N ${Math.abs(lon).toFixed(4)}${lon < 0 ? "W" : "E"}`;
}

function Corners({ className = "border-white/70" }: { className?: string }) {
  return (
    <div className="pointer-events-none absolute inset-3" aria-hidden>
      <span className={`hud-corner left-0 top-0 border-l border-t ${className}`} />
      <span className={`hud-corner right-0 top-0 border-r border-t ${className}`} />
      <span className={`hud-corner bottom-0 left-0 border-b border-l ${className}`} />
      <span className={`hud-corner bottom-0 right-0 border-b border-r ${className}`} />
    </div>
  );
}

export function Player({ source, onSourceChange, ctx, positionBadge, sync, remote, size = "default", onCamStream }: PlayerProps) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const [camStream, setCamStream] = useState<MediaStream | null>(null);
  const session = useMemo(() => sessionFromUrl(), []);
  usePhonePublisher(camStream, session);
  useEffect(() => {
    onCamStream?.(camStream);
  }, [camStream, onCamStream]);
  const [error, setError] = useState<string | null>(null);
  const [camError, setCamError] = useState<string | null>(null);
  const [retryKey, setRetryKey] = useState(0);
  const [facing, setFacing] = useState<Facing>("environment");
  const now = useClock();

  useEffect(() => {
    if (source.kind !== "webcam") return;
    const video = videoRef.current;
    let stream: MediaStream | null = null;
    let cancelled = false;
    const insecure = !navigator.mediaDevices?.getUserMedia;
    (insecure
      ? Promise.reject(new Error("insecure"))
      : navigator.mediaDevices.getUserMedia({ video: { facingMode: { ideal: facing }, width: { ideal: 1280 }, height: { ideal: 720 } }, audio: false })
    )
      .then((s) => {
        if (cancelled) {
          s.getTracks().forEach((t) => t.stop());
          return;
        }
        stream = s;
        setCamStream(s);
        if (video) video.srcObject = s;
      })
      .catch((e: unknown) => {
        if (cancelled) return;
        const denied = e instanceof DOMException && e.name === "NotAllowedError";
        setCamError(
          insecure
            ? "The browser blocks the camera on plain http. Open the https link (tunnel) or localhost."
            : denied
              ? "Camera access was denied. Allow it in your browser and try again."
              : "No camera available.",
        );
        onSourceChange({ kind: "idle" });
      });
    return () => {
      cancelled = true;
      setCamStream(null);
      stream?.getTracks().forEach((t) => t.stop());
      if (video) video.srcObject = null;
    };
  }, [source.kind, retryKey, facing, onSourceChange]);

  useEffect(() => {
    if (source.kind !== "file") return;
    const url = source.url;
    return () => URL.revokeObjectURL(url);
  }, [source]);

  const change = (s: StreamSource) => {
    setError(null);
    setCamError(null);
    onSourceChange(s);
  };

  const sourceLabel =
    source.kind === "webcam" ? (facing === "user" ? "Selfie camera" : "Rear camera") : source.kind === "idle" ? "" : source.label;
  const walking = source.kind === "webcam";
  const frame =
    size === "full"
      ? "h-full rounded-none"
      : size === "wide"
        ? "mx-auto aspect-video w-[min(100%,calc((100dvh-13rem)*16/9))] rounded-[28px]"
        : `rounded-[28px] ${walking || source.kind === "idle" ? "aspect-[3/4] sm:aspect-video" : "aspect-video"}`;

  return (
    <div
      className={`relative w-full overflow-hidden bg-black shadow-[0_0_0_1px_rgb(255_255_255/0.08),0_30px_70px_-24px_rgb(0_0_0/0.85)] transition-[height,border-radius] duration-300 ${frame}`}
    >
      {source.kind === "idle" ? (
        remote && remote.status !== "off" ? (
          <PhoneFeed remote={remote} corners={<Corners />} positionBadge={positionBadge} sync={sync} />
        ) : (
          <IdleCard onPick={change} camError={camError} ctx={ctx} now={now} positionBadge={positionBadge} />
        )
      ) : (
        <>
          <video
            key={`${source.kind === "webcam" ? `cam-${facing}` : source.url}-${retryKey}`}
            ref={videoRef}
            src={source.kind === "webcam" ? undefined : source.url}
            autoPlay
            muted
            loop
            playsInline
            onError={() => setError("The stream stopped responding.")}
            className={`size-full ${size === "full" && !walking ? "object-contain" : "object-cover"} ${walking && facing === "user" ? "-scale-x-100" : ""}`}
          />
          <div className="grain scanlines pointer-events-none absolute inset-0" aria-hidden />
          <div className="pointer-events-none absolute inset-x-0 top-0 h-28 bg-gradient-to-b from-black/70 to-transparent" />
          <div className="pointer-events-none absolute inset-x-0 bottom-0 h-24 bg-gradient-to-t from-black/70 to-transparent" />
          <Corners />

          <div className="absolute left-4 top-4 flex items-center gap-2 font-mono text-[13px] sm:left-6 sm:top-6 sm:text-[11px]">
            <span className="live-badge">
              <span className="animate-live size-1.5 rounded-full bg-white" />
              Live
            </span>
            <span className="flex h-[1.6rem] items-center gap-1.5 rounded-full bg-black/55 px-3 font-bold text-white shadow-[inset_0_1px_2px_rgb(255_255_255/0.2)] backdrop-blur-md">
              <span className="size-1.5 rounded-full bg-candy-orange" />
              {formatViewers(ctx.viewers)}
              <span className="hidden text-white/50 sm:inline">watching</span>
            </span>
          </div>

          <div className="absolute right-4 top-4 flex flex-col items-end gap-1 font-mono text-[11px] text-white sm:right-6 sm:top-6">
            {now && (
              <span className="rounded-[4px] border border-white/15 bg-black/50 px-2 py-1 tracking-normal backdrop-blur-sm">
                {now.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", second: "2-digit" })}
                <span className="ml-1.5 hidden text-white/45 sm:inline">BST</span>
              </span>
            )}
            {positionBadge && (
              <span className="rounded-[4px] border border-white/15 bg-black/50 px-2 py-1 text-[10px] tracking-normal text-white/80 backdrop-blur-sm">
                {positionBadge}
              </span>
            )}
            {sync && (
              <span className="flex items-center gap-1.5 rounded-[4px] border border-white/15 bg-black/50 px-2 py-1 text-[10px] tracking-normal text-white/80 backdrop-blur-sm">
                <span className={`size-1.5 rounded-full ${sync.includes("connected") || sync.includes("following") ? "bg-emerald-400" : "bg-amber-400"}`} />
                {sync}
              </span>
            )}
            {walking && (
              <button
                type="button"
                aria-label="Flip camera"
                onClick={() => setFacing((f) => (f === "user" ? "environment" : "user"))}
                className="mt-1 grid size-11 place-items-center rounded-full border border-white/20 bg-black/50 text-white backdrop-blur-sm active:bg-white/20 sm:size-8"
              >
                <SwitchCamera className="size-5 sm:size-4" aria-hidden />
              </button>
            )}
          </div>

          <div className="absolute inset-x-4 bottom-4 flex items-end justify-between gap-3 sm:inset-x-6 sm:bottom-6">
            <div className="min-w-0">
              <p className="font-display text-[22px] font-semibold leading-none tracking-[-0.02em] text-white sm:text-[24px]">
                {ctx.location.name}
              </p>
              <p className="mt-1.5 flex items-center gap-2 font-mono text-[11px] tracking-normal text-white/60 sm:text-[10px]">
                <span className="text-ember">{ctx.location.area}</span>
                <span className="hidden sm:inline">{coords(ctx.location.lat, ctx.location.lon)}</span>
                <span className="hidden text-white/40 md:inline">· {sourceLabel}</span>
              </p>
            </div>
            <button
              type="button"
              onClick={() => change({ kind: "idle" })}
              className="pill glass !h-11 !px-4 hover:!bg-none hover:!bg-live sm:!h-9"
            >
              <Square className="size-2.5 fill-current" aria-hidden />
              End
            </button>
          </div>

          {error && (
            <div className="absolute inset-0 grid place-items-center bg-black/80 backdrop-blur-sm">
              <div className="flex flex-col items-center gap-3 text-center">
                <p className="font-mono text-[11px] tracking-normal text-live">Signal lost</p>
                <p className="text-sm text-text">{error}</p>
                <button
                  type="button"
                  onClick={() => {
                    setError(null);
                    setRetryKey((k) => k + 1);
                  }}
                  className="flex items-center gap-1.5 rounded-md bg-ember px-3 py-1.5 text-sm font-semibold text-bg"
                >
                  <RotateCcw className="size-3.5" aria-hidden />
                  Retry
                </button>
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}

function IdleCard({
  onPick,
  camError,
  ctx,
  now,
  positionBadge,
}: {
  onPick: (s: StreamSource) => void;
  camError: string | null;
  ctx: StreamContext;
  now: Date | null;
  positionBadge?: string;
}) {
  const fileRef = useRef<HTMLInputElement>(null);

  return (
    <div className="relative size-full bg-surface">
      <div className="hud-grid absolute inset-0" aria-hidden />
      <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(ellipse_60%_80%_at_50%_120%,var(--ember-soft),transparent)]" aria-hidden />
      <Corners className="border-white/25" />

      <div className="absolute left-6 top-6 flex items-center gap-2 font-mono text-[10px] tracking-normal text-muted">
        <span className="size-1.5 rounded-full bg-dim" />
        Standby
      </div>
      <div className="absolute right-6 top-6 hidden font-mono text-[10px] tracking-normal text-muted sm:block">
        {ctx.location.area} · {coords(ctx.location.lat, ctx.location.lon)}
        {positionBadge ? ` · ${positionBadge}` : ""}
      </div>
      {now && (
        <div className="absolute bottom-6 right-6 hidden font-mono text-[10px] tracking-normal text-dim sm:block">
          {now.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", second: "2-digit" })}
        </div>
      )}

      <div className="relative grid size-full place-items-center p-6 sm:p-10">
        <div className="w-full max-w-xl">
          <p className="font-mono text-[10px] tracking-normal text-ember">No signal</p>
          <h2 className="mt-2 font-display text-[28px] font-semibold leading-[1.05] tracking-[-0.03em] sm:text-[36px]">
            Go live from {ctx.location.name}.
          </h2>
          <p className="mt-2 max-w-md text-[13px] leading-5 text-muted">
            Pick a feed. Chat and the city watch the moment you open the signal.
          </p>

          <div className="mt-6 grid gap-2 sm:grid-cols-3">
            <SourceButton
              index="01"
              icon={<Camera className="size-4" />}
              title="Go live"
              hint="Rear camera + GPS"
              onClick={() => onPick({ kind: "webcam" })}
            />
            <SourceButton
              index="02"
              icon={<Film className="size-4" />}
              title="TfL JamCam"
              hint="Nearest street cam"
              accent
              onClick={() => {
                onPick({ kind: "url", url: ctx.location.camera.url, label: ctx.location.camera.label });
              }}
            />
            <SourceButton
              index="03"
              icon={<Upload className="size-4" />}
              title="Upload"
              hint="IRL recording"
              onClick={() => fileRef.current?.click()}
            />
          </div>


          <input
            ref={fileRef}
            type="file"
            accept="video/*"
            className="hidden"
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) onPick({ kind: "file", url: URL.createObjectURL(f), label: f.name });
              e.target.value = "";
            }}
          />

          {camError && (
            <p className="mt-4 rounded-[4px] border border-live/30 bg-live/10 px-3 py-2 text-xs text-live">{camError}</p>
          )}
        </div>
      </div>
    </div>
  );
}

function SourceButton({
  index,
  icon,
  title,
  hint,
  accent,
  onClick,
}: {
  index: string;
  icon: React.ReactNode;
  title: string;
  hint: string;
  accent?: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`group flex items-center gap-3 rounded-[24px] px-3.5 py-3 text-left transition-transform duration-200 ease-[var(--ease-spring)] hover:-translate-y-0.5 hover:scale-[1.03] active:scale-95 sm:flex-col sm:items-start sm:gap-4 sm:py-4 ${
        accent
          ? "bg-[linear-gradient(135deg,rgb(255_42_149/0.28),rgb(255_148_25/0.22))] shadow-[inset_0_2px_3px_rgb(255_255_255/0.3),inset_0_-4px_8px_rgb(140_0_70/0.3),0_14px_28px_-10px_rgb(236_17_135/0.55)]"
          : "tile"
      }`}
    >
      <span className="flex w-full items-center justify-between sm:w-auto">
        <span className={`grid size-9 place-items-center rounded-full ${accent ? "bg-[image:var(--grad-candy)] text-white shadow-[inset_0_2px_3px_rgb(255_255_255/0.5)]" : "bg-white/10 text-text"}`}>
          {icon}
        </span>
      </span>
      <span className="min-w-0 flex-1">
        <span className="flex items-center justify-between gap-2">
          <span className="text-[13px] font-semibold">{title}</span>
          <span className="font-mono text-[9px] tracking-normal text-dim">{index}</span>
        </span>
        <span className="block text-[11px] text-muted">{hint}</span>
      </span>
    </button>
  );
}
