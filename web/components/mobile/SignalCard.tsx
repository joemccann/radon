"use client";

type Tone = "pos" | "neg" | "warn" | "mut";

type SignalPill = {
  label: string;
  tone: Tone;
};

type StatItem = {
  label: string;
  value: string;
};

type SignalCardProps = {
  ticker: string;
  score: number;
  signals: SignalPill[];
  stats: StatItem[];
  onPress?: () => void;
};

function scoreTone(score: number): "pos" | "warn" | "neg" {
  if (score > 60) return "pos";
  if (score >= 40) return "warn";
  return "neg";
}

export function SignalCard({ ticker, score, signals, stats, onPress }: SignalCardProps) {
  const tone = scoreTone(score);

  return (
    <div
      className="m-signal-card"
      role={onPress ? "button" : undefined}
      tabIndex={onPress ? 0 : undefined}
      onClick={onPress}
      onKeyDown={onPress ? (e) => { if (e.key === "Enter" || e.key === " ") onPress(); } : undefined}
    >
      <div className="m-signal-card__head">
        <span className="m-signal-card__ticker">{ticker}</span>
        <span className={`m-signal-card__score m-signal-card__score--${tone}`}>{score}</span>
      </div>

      {signals.length > 0 && (
        <div className="m-signal-card__signals">
          {signals.map((sig, i) => (
            <span key={i} className={`m-pill m-pill--${sig.tone}`}>{sig.label}</span>
          ))}
        </div>
      )}

      {stats.length > 0 && (
        <div className="m-signal-card__stats">
          {stats.map((s, i) => (
            <div key={i} className="m-metric">
              <span className="m-metric__label">{s.label}</span>
              <span className="m-metric__value m-metric__value--secondary">{s.value}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export default SignalCard;
