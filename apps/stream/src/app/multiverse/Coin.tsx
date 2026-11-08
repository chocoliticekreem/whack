import { Fragment, useId } from "react";

/** The W play-money token: an amber coin with a bold W. It stands in for the letter W before an amount. */
export function Coin({ size = 15 }: { size?: number }) {
  const id = useId();
  return (
    <svg
      className="w-coin"
      width={size}
      height={size}
      viewBox="0 0 16 16"
      role="img"
      aria-label="W"
      style={{ display: "inline-block", flex: "none", verticalAlign: "-0.17em", marginRight: "0.12em" }}
    >
      <defs>
        <linearGradient id={id} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="#ffe08a" />
          <stop offset="1" stopColor="#f2a516" />
        </linearGradient>
      </defs>
      <circle cx="8" cy="8" r="7.6" fill="#a8650a" />
      <circle cx="8" cy="8" r="6.5" fill={`url(#${id})`} />
      <circle cx="8" cy="8" r="5.5" fill="none" stroke="#c47d0e" strokeWidth="0.6" opacity="0.75" />
      <path d="M4.3 5.3 5.75 10.7 8 7.2l2.25 3.5 1.45-5.4" fill="none" stroke="#5c3300" strokeWidth="1.55" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

/** Text with each "W<digits>" drawn as the coin, e.g. "+W70" or "Refunded W100". */
export function CoinText({ text, size }: { text: string; size?: number }) {
  const parts = text.split(/W(?=\d)/);
  return (
    <>
      {parts.map((p, i) => (
        <Fragment key={i}>
          {i > 0 && <Coin size={size} />}
          {p}
        </Fragment>
      ))}
    </>
  );
}
