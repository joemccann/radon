import type { CatalystData, CatalystRow } from "@/lib/useCatalysts";
import { marketDateKey, shiftDateKey } from "./time";

type SampleCatalyst = Omit<CatalystRow, "date" | "days_until" | "source"> & { offsetDays: number };

const SAMPLE_CATALYSTS: SampleCatalyst[] = [
  { offsetDays: 0, ticker: null, type: "economic", title: "Sample retail sales print", event_time: "08:30", forecast: "0.3%", prev: "0.2%" },
  { offsetDays: 1, ticker: "NVDA", type: "earnings", title: "NVDA sample earnings", event_time: "AMC", street_mean_est: "0.92", expected_move_perc: "7.8" },
  { offsetDays: 2, ticker: null, type: "economic", title: "Sample jobless claims", event_time: "08:30", forecast: "221K", prev: "218K" },
  { offsetDays: 3, ticker: "TSLA", type: "earnings", title: "TSLA sample earnings", event_time: "AMC", street_mean_est: "0.58", expected_move_perc: "9.4" },
  { offsetDays: 6, ticker: "MRNA", type: "fda", title: "MRNA sample advisory committee date" },
  { offsetDays: 8, ticker: "AAPL", type: "earnings", title: "AAPL sample earnings", event_time: "AMC", street_mean_est: "1.71", expected_move_perc: "4.2" },
  { offsetDays: 9, ticker: null, type: "economic", title: "Sample CPI release", event_time: "08:30", forecast: "0.2%", prev: "0.3%" },
];

/** Request-time catalyst feed sample for /api/catalysts. No Turso or disk. */
export function buildDemoCatalysts(now: Date = new Date()): CatalystData {
  const today = marketDateKey(now);
  const catalysts: CatalystRow[] = SAMPLE_CATALYSTS.map(({ offsetDays, ...row }) => ({
    ...row,
    date: shiftDateKey(today, offsetDays),
    days_until: offsetDays,
    source: "Sample data",
  }));
  return { scan_time: now.toISOString(), count: catalysts.length, catalysts };
}
