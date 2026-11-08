"use client";

import { useState } from "react";
import { Coin } from "./Coin";
import { PinInput } from "./PinInput";
import { recoverPlayer, setNickname, setPin, type Recovered } from "./Wallet";

const NAME_MAX = 18;
const ADJECTIVES = ["Swift", "Lucky", "Bold", "Sly", "Rapid", "Quiet", "Brave", "Clever", "Sunny", "Rainy", "Neon", "Foggy"];
const ANIMALS = ["Fox", "Otter", "Pigeon", "Heron", "Badger", "Owl", "Hare", "Raven", "Swan", "Robin", "Stag", "Seal"];
const pick = <T,>(xs: T[]) => xs[Math.floor(Math.random() * xs.length)];

export const funName = () => `${pick(ADJECTIVES)} ${pick(ANIMALS)} ${Math.floor(Math.random() * 90) + 10}`;

/** Name + PIN for the device's player, with up to 3 fresh fun names if the chosen one is taken by someone else. */
async function claim(player: string, token: string, name: string, generated: boolean): Promise<{ name: string } | { error: string }> {
  for (let tries = 0; ; tries++) {
    const r = await setNickname(player, token, name);
    if (r.ok) return { name };
    if (r.code !== "name_taken") return { error: r.error ?? "Could not save the name" };
    if (!generated || tries >= 3) return { error: "That name is taken. Try another." };
    name = funName();
  }
}

/** First visit: pick a name (and optionally a PIN), skip, or sign in with name + PIN from another device. */
export function Welcome({
  player,
  token,
  mode: initial,
  onNamed,
  onRecovered,
  onClose,
}: {
  player: string;
  token: string | null;
  mode: "new" | "recover";
  onNamed: (name: string) => void;
  onRecovered: (r: Recovered) => void;
  onClose?: () => void;
}) {
  const [mode, setMode] = useState(initial);
  const [name, setName] = useState("");
  const [pin, setPinValue] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [placeholder] = useState(funName);

  const start = async (skip: boolean) => {
    if (!token) return;
    if (!skip && pin.length > 0 && pin.length < 4) return setError("A PIN is 4 digits. Or leave it empty.");
    setBusy(true);
    setError(null);
    const typed = skip ? "" : name.trim().slice(0, NAME_MAX);
    const r = await claim(player, token, typed || placeholder, !typed);
    if ("error" in r) {
      setBusy(false);
      return setError(r.error);
    }
    if (!skip && pin.length === 4) {
      const p = await setPin(player, token, pin);
      if (!p.ok) {
        setBusy(false);
        setName(r.name);
        return setError(p.error ?? "Could not save the PIN");
      }
    }
    onNamed(r.name);
  };

  const signIn = async () => {
    if (!name.trim() || pin.length !== 4) return setError("Enter your name and 4-digit PIN.");
    setBusy(true);
    setError(null);
    const r = await recoverPlayer(name.trim(), pin);
    setBusy(false);
    if ("error" in r) return setError(r.error);
    onRecovered(r);
  };

  const switchTo = (next: "new" | "recover") => {
    setMode(next);
    setError(null);
    setPinValue("");
  };

  return (
    <div className="tl-modal-bg tl-welcome-bg" onClick={() => mode === "recover" && onClose?.()}>
      <form
        className="tl-modal tl-welcome"
        onClick={(e) => e.stopPropagation()}
        onSubmit={(e) => {
          e.preventDefault();
          if (!busy) void (mode === "new" ? start(false) : signIn());
        }}
      >
        {mode === "new" ? (
          <>
            <h2>Welcome to OverWatch</h2>
            <p className="tl-muted">
              You start with <Coin />
              1,000. W are play money, no real value.
            </p>
            <label className="tl-field">
              <span>Your name</span>
              <input value={name} onChange={(e) => setName(e.target.value)} maxLength={NAME_MAX} placeholder={placeholder} autoFocus aria-invalid={!!error || undefined} />
            </label>
            <div className="tl-field">
              <span>
                PIN <span className="tl-muted">optional, to play on another device</span>
              </span>
              <PinInput value={pin} onChange={setPinValue} />
            </div>
            {error && <p className="tl-field-err" role="alert">{error}</p>}
            <button type="submit" className="tl-confirm" disabled={busy || !token}>
              {token ? "Start playing" : "Setting up…"}
            </button>
            <div className="tl-welcome-alt">
              <button type="button" className="tl-link" disabled={busy || !token} onClick={() => void start(true)}>
                Skip
              </button>
              <button type="button" className="tl-link" onClick={() => switchTo("recover")}>
                Already playing?
              </button>
            </div>
          </>
        ) : (
          <>
            <h2>Welcome back</h2>
            <p className="tl-muted">Your name and PIN from your other device.</p>
            <label className="tl-field">
              <span>Your name</span>
              <input value={name} onChange={(e) => setName(e.target.value)} maxLength={NAME_MAX} autoFocus autoCapitalize="words" />
            </label>
            <div className="tl-field">
              <span>PIN</span>
              <PinInput value={pin} onChange={setPinValue} />
            </div>
            {error && <p className="tl-field-err" role="alert">{error}</p>}
            <button type="submit" className="tl-confirm" disabled={busy}>
              Sign in
            </button>
            <div className="tl-welcome-alt">
              {onClose ? (
                <button type="button" className="tl-link" onClick={onClose}>
                  Cancel
                </button>
              ) : (
                <button type="button" className="tl-link" onClick={() => switchTo("new")}>
                  New player
                </button>
              )}
            </div>
          </>
        )}
      </form>
    </div>
  );
}
