/**
 * The CRI reading /regime is showing, in the shape generate_regime_share.py
 * lays over its scan snapshot. Null while the page shows the snapshot itself.
 */

type CriReading = {
  score: number;
  level: string;
  components: { vix: number; vvix: number; correlation: number; momentum: number };
};

export type ShownRegimeValues = {
  liveCri: CriReading | null;
  vix: number | null;
  vvix: number | null;
  spy: number | null;
  cor1m: number | null;
  cor1m5dChange: number | null;
  vvixVixRatio: number | null;
  spxDistancePct: number | null;
  realizedVol: number | null;
  spxBelowMa: boolean;
  cor1mAboveThreshold: boolean;
};

export type RegimeShareReading = Record<string, unknown>;

export function buildRegimeShareReading(shown: ShownRegimeValues): RegimeShareReading | null {
  if (!shown.liveCri) return null;
  return {
    ...finiteFields({
      vix: shown.vix,
      vvix: shown.vvix,
      spy: shown.spy,
      cor1m: shown.cor1m,
      cor1m_5d_change: shown.cor1m5dChange,
      vvix_vix_ratio: shown.vvixVixRatio,
      spx_distance_pct: shown.spxDistancePct,
      realized_vol: shown.realizedVol,
    }),
    cri: shown.liveCri,
    crash_trigger: {
      conditions: { spx_below_100d_ma: shown.spxBelowMa, cor1m_gt_60: shown.cor1mAboveThreshold },
    },
  };
}

function finiteFields(fields: Record<string, number | null>): Record<string, number> {
  return Object.fromEntries(
    Object.entries(fields).filter((entry): entry is [string, number] => Number.isFinite(entry[1])),
  );
}
