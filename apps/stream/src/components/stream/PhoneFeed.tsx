"use client";

import { useEffect, useRef } from "react";
import type { PhoneVideo } from "@/platform/live/rtc/usePhoneVideo";

/** Attaches a MediaStream to a muted, inline, autoplaying video element. */
export function StreamVideo({ stream, className }: { stream: MediaStream; className?: string }) {
  const ref = useRef<HTMLVideoElement>(null);
  useEffect(() => {
    const v = ref.current;
    if (!v) return;
    v.srcObject = stream;
    void v.play().catch(() => {});
    return () => {
      v.srcObject = null;
    };
  }, [stream]);
  return <video ref={ref} autoPlay muted playsInline data-phone-video className={className} />;
}

/** The stream as a small preview for the map and twin views: a live MediaStream, or the clip/upload that is playing. */
export function FeedPreview({
  stream,
  clipUrl,
  label,
  viewers,
}: {
  stream: MediaStream | null;
  clipUrl: string | null;
  label: string;
  viewers: number;
}) {
  return (
    <div className="relative size-full bg-black">
      {stream ? (
        <StreamVideo stream={stream} className="size-full object-cover" />
      ) : (
        <video src={clipUrl ?? undefined} autoPlay muted loop playsInline className="size-full object-cover" />
      )}
      <div className="pointer-events-none absolute inset-x-0 top-0 h-16 bg-gradient-to-b from-black/60 to-transparent" />
      <div className="pointer-events-none absolute left-2.5 top-2.5 flex items-center gap-1.5">
        <span className="live-badge">
          <span className="animate-live size-1.5 rounded-full bg-white" />
          Live
        </span>
        <span className="flex h-[1.6rem] items-center rounded-full bg-black/55 px-2.5 text-[11px] font-bold text-white backdrop-blur-sm">
          {viewers.toLocaleString("en-GB")}
        </span>
      </div>
      <p className="pointer-events-none absolute inset-x-3 bottom-2.5 truncate text-[12px] font-bold text-white/90 drop-shadow">
        {label}
      </p>
    </div>
  );
}

export function phoneChip(remote: PhoneVideo) {
  if (remote.status === "connected" && remote.stream) {
    return remote.latencyS === null ? "Live from phone" : `Live from phone · ${remote.latencyS.toFixed(1)} s`;
  }
  return remote.status === "failed" ? "Phone video unavailable" : "Connecting to phone…";
}

/** Laptop view of the phone's camera: the Live tab shows this while following the phone. */
export function PhoneFeed({
  remote,
  corners,
  positionBadge,
  sync,
}: {
  remote: PhoneVideo;
  corners: React.ReactNode;
  positionBadge?: string;
  sync?: string;
}) {
  const connected = remote.status === "connected" && !!remote.stream;
  return (
    <div className="relative size-full bg-black">
      {remote.stream ? (
        <StreamVideo stream={remote.stream} className="size-full object-cover" />
      ) : (
        <div className="hud-grid absolute inset-0" aria-hidden />
      )}
      <div className="grain scanlines pointer-events-none absolute inset-0" aria-hidden />
      <div className="pointer-events-none absolute inset-x-0 top-0 h-28 bg-gradient-to-b from-black/70 to-transparent" />
      <div className="pointer-events-none absolute inset-x-0 bottom-0 h-24 bg-gradient-to-t from-black/70 to-transparent" />
      {corners}

      <div className="absolute left-4 top-4 flex items-center gap-2 font-mono text-[11px] sm:left-6 sm:top-6">
        <span
          className={`flex h-6 items-center gap-1.5 rounded-[4px] px-2 font-semibold tracking-normal text-white ${
            connected ? "bg-live" : "bg-black/50 border border-white/15"
          }`}
          data-phone-chip
        >
          <span className={`size-1.5 rounded-full bg-white ${connected ? "animate-live" : ""}`} />
          {phoneChip(remote)}
        </span>
        <span className="flex h-6 items-center rounded-[4px] border border-white/15 bg-black/50 px-2 tracking-normal text-white/70 backdrop-blur-sm">
          WebRTC · {remote.status}
        </span>
      </div>

      <div className="absolute right-4 top-4 flex flex-col items-end gap-1 font-mono text-[10px] text-white/80 sm:right-6 sm:top-6">
        {positionBadge && (
          <span className="rounded-[4px] border border-white/15 bg-black/50 px-2 py-1 tracking-normal backdrop-blur-sm">{positionBadge}</span>
        )}
        {sync && (
          <span className="flex items-center gap-1.5 rounded-[4px] border border-white/15 bg-black/50 px-2 py-1 tracking-normal backdrop-blur-sm">
            <span className="size-1.5 rounded-full bg-emerald-400" />
            {sync}
          </span>
        )}
      </div>

      {!remote.stream && (
        <div className="absolute inset-0 grid place-items-center">
          <p className="font-mono text-[11px] tracking-normal text-muted">
            {remote.status === "failed" ? "Phone video unavailable (state only)" : "Waiting for the phone's camera…"}
          </p>
        </div>
      )}
    </div>
  );
}
