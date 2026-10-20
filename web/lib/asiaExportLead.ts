/** Official Taiwan and South Korea total-export monthly snapshot. */
export interface AsiaExportLeadEntry {
  date: string;
  lead_date: string;
  taiwan_exports_usd_mn: number | null;
  korea_exports_usd_mn: number | null;
  taiwan_yoy_pct: number | null;
  korea_yoy_pct: number | null;
  mean_yoy_pct: number | null;
  ma3_yoy_pct: number | null;
  ma3_change_pp: number | null;
}

export interface AsiaExportLeadCurrent extends AsiaExportLeadEntry {
  growth: "expanding" | "contracting" | "flat" | "unavailable";
  momentum: "accelerating" | "slowing" | "unchanged" | "unavailable";
}

export interface AsiaExportLeadSource {
  name: string;
  url: string;
  licence_url: string;
  latest_month: string;
  last_modified: string | null;
  sampled_at: string;
}

export interface AsiaExportLeadData {
  missing: boolean;
  stale?: boolean;
  scan_time: string | null;
  source_last_modified: string | null;
  data_date: string | null;
  count: number;
  series: AsiaExportLeadEntry[];
  current: AsiaExportLeadCurrent | null;
  sources: { taiwan: AsiaExportLeadSource; korea: AsiaExportLeadSource } | null;
  comparison: {
    available: false;
    label: "MSCI US forward 12-month EPS";
    reason: "Licensed EPS series not connected";
  };
}
