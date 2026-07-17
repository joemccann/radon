import type { OptionsExposurePayload } from "@/lib/optionsExposure";

export const EXPOSURE_FIXTURE: OptionsExposurePayload = {
  schema_version: 1,
  symbol: "MU",
  source: "menthorq",
  source_time: "2026-07-16T20:00:00",
  fetched_at: "2026-07-16T20:01:00Z",
  frequency: "eod",
  spot: 102,
  strikes: [90, 95, 100, 105, 110],
  expirations: [
    { expiration_date: "2026-07-17", dte: 1 },
    { expiration_date: "2026-07-24", dte: 8 },
  ],
  cells: {
    net_gex: [-1, 2, -3, 4, 5, -6],
    abs_gex: [1, 2, 3, 4, 5, 6],
    net_dex: [-10, 20, -30, 40, 50, -60],
    abs_dex: [10, 20, 30, 40, 50, 60],
    oi_call: [10, 25, 30, 10, 50, 5],
    oi_put: [30, 5, 10, 40, 20, 15],
    strike_idx: [0, 0, 1, 1, 2, 2],
    expiration_idx: [0, 1, 0, 1, 0, 1],
  },
  levels: [
    { key: "hvl", label: "HVL", value: 100 },
    { key: "max_1d", label: "1D Max", value: 109.5 },
  ],
  units: { net_gex: "usd", net_dex: "usd", open_interest: "contracts" },
  complete: true,
};
