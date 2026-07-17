"use client";

import { useMemo, useState, type CSSProperties } from "react";

import {
  DEFAULT_EXPOSURE_CONTROLS,
  EXPOSURE_LEVEL_OPTIONS,
  EXPOSURE_METRIC_OPTIONS,
  EXPOSURE_STRIKE_OPTIONS,
  aggregateOptionsExposure,
  exposureMetricLabel,
  isAbsoluteExposureMetric,
  nearestStrike,
  selectStrikeWindow,
  type OptionsExposureFrequency,
  type OptionsExposureLevel,
  type OptionsExposureLevelKey,
  type OptionsExposureMetric,
  type OptionsExposureStrikeWindow,
} from "@/lib/optionsExposure";
import { useOptionsExposure } from "@/lib/useOptionsExposure";

import SpectralLoader from "./SpectralLoader";
import styles from "./OptionsExposurePanel.module.css";

interface OptionsExposurePanelProps {
  symbol: string;
}

type WidthStyle = CSSProperties & { "--bar-width": string };

function formatExpiration(date: string): string {
  const parsed = new Date(`${date}T00:00:00Z`);
  if (Number.isNaN(parsed.valueOf())) return date;
  return parsed.toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "UTC" });
}

function formatTimestamp(value: string): string {
  // ClickHouse returns UTC timestamps without a trailing zone marker. Treat
  // that provider format as UTC instead of letting each browser reinterpret
  // it in the operator's local timezone.
  const zonedValue = /(?:Z|[+-]\d{2}:\d{2})$/i.test(value) ? value : `${value}Z`;
  const parsed = new Date(zonedValue);
  if (Number.isNaN(parsed.valueOf())) return value;
  return parsed.toLocaleString("en-US", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
    timeZone: "UTC",
    timeZoneName: "short",
  });
}

function compactNumber(value: number): string {
  const absolute = Math.abs(value);
  if (absolute >= 1_000_000_000) return `${(absolute / 1_000_000_000).toFixed(2)}B`;
  if (absolute >= 1_000_000) return `${(absolute / 1_000_000).toFixed(2)}M`;
  if (absolute >= 1_000) return `${(absolute / 1_000).toFixed(1)}K`;
  return absolute.toLocaleString("en-US", { maximumFractionDigits: 2 });
}

function formatValue(value: number, metric: OptionsExposureMetric): string {
  const sign = value > 0 ? "+" : value < 0 ? "-" : "";
  const unit = metric === "open_interest" ? "" : "$";
  return `${sign}${unit}${compactNumber(value)}`;
}

function formatStrike(value: number): string {
  return value.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function levelRows(
  levels: OptionsExposureLevel[],
  allStrikes: number[],
  visibleStrikes: number[],
): Map<number, OptionsExposureLevel[]> {
  const mapped = new Map<number, OptionsExposureLevel[]>();
  const visible = new Set(visibleStrikes);
  for (const level of levels) {
    if (level.value == null || !Number.isFinite(level.value)) continue;
    const strike = nearestStrike(allStrikes, level.value);
    if (strike == null || !visible.has(strike)) continue;
    const existing = mapped.get(strike) ?? [];
    existing.push(level);
    mapped.set(strike, existing);
  }
  return mapped;
}

function MeasurementState({
  kind,
  message,
  onRetry,
}: {
  kind: "error" | "empty";
  message: string;
  onRetry?: () => void;
}) {
  return (
    <section
      className={styles.panel}
      data-testid="options-exposure-panel"
      data-responsive="instrument"
      aria-label="Options exposure"
    >
      <div className={styles.state} role={kind === "error" ? "alert" : "status"}>
        <span className={styles.stateLabel}>{kind === "error" ? "MEASUREMENT FAULT" : "OPTIONS / EXPOSURE"}</span>
        <p>{message}</p>
        {onRetry ? <button type="button" className={styles.retry} onClick={onRetry}>Retry measurement</button> : null}
      </div>
    </section>
  );
}

export default function OptionsExposurePanel({ symbol }: OptionsExposurePanelProps) {
  const normalizedSymbol = symbol.trim().toUpperCase();
  const [metric, setMetric] = useState<OptionsExposureMetric>(DEFAULT_EXPOSURE_CONTROLS.metric);
  const [strikeWindow, setStrikeWindow] = useState<OptionsExposureStrikeWindow>(DEFAULT_EXPOSURE_CONTROLS.strikeWindow);
  const [frequency, setFrequency] = useState<OptionsExposureFrequency>(DEFAULT_EXPOSURE_CONTROLS.frequency);
  const [expirationDates, setExpirationDates] = useState<Set<string> | null>(DEFAULT_EXPOSURE_CONTROLS.expirationDates);
  const [visibleLevels, setVisibleLevels] = useState<Set<OptionsExposureLevelKey>>(
    () => new Set(DEFAULT_EXPOSURE_CONTROLS.visibleLevels),
  );
  const { data, loading, error, refresh } = useOptionsExposure(normalizedSymbol, frequency);

  const rows = useMemo(() => {
    if (!data) return [];
    return selectStrikeWindow(
      aggregateOptionsExposure(data, metric, expirationDates),
      data.spot,
      strikeWindow,
    );
  }, [data, expirationDates, metric, strikeWindow]);

  const spotStrike = data ? nearestStrike(data.strikes, data.spot) : null;
  const visibleStrikes = useMemo(() => rows.map((row) => row.strike), [rows]);
  const levelsByStrike = useMemo(() => {
    if (!data) return new Map<number, OptionsExposureLevel[]>();
    return levelRows(
      data.levels.filter((level) => visibleLevels.has(level.key)),
      data.strikes,
      visibleStrikes,
    );
  }, [data, visibleLevels, visibleStrikes]);
  const maxExposure = Math.max(1, ...rows.map((row) => Math.abs(row.value)));
  const maxOi = Math.max(1, ...rows.flatMap((row) => [row.putOi, row.callOi]));

  if (loading && !data) {
    return (
      <section className={styles.panel} data-testid="options-exposure-panel" data-responsive="instrument" aria-label="Options exposure">
        <div className={styles.state}>
          <SpectralLoader label={`Sampling options exposure for ${normalizedSymbol}.`} />
        </div>
      </section>
    );
  }
  if (error && !data) {
    return <MeasurementState kind="error" message={error} onRetry={() => void refresh()} />;
  }
  if (!data) {
    return <MeasurementState kind="empty" message={`No exposure measurement returned for ${normalizedSymbol}.`} />;
  }

  const selectExpiration = (expirationDate: string) => {
    setExpirationDates((current) => (
      current?.has(expirationDate) ? null : new Set([expirationDate])
    ));
  };

  const toggleLevel = (key: OptionsExposureLevelKey) => {
    setVisibleLevels((current) => {
      const next = new Set(current);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  return (
    <section
      className={styles.panel}
      data-testid="options-exposure-panel"
      data-responsive="instrument"
      aria-labelledby="options-exposure-heading"
    >
      <header className={styles.header}>
        <div>
          <div className={styles.eyebrow}>OPTIONS / EXPOSURE</div>
          <div className={styles.titleLine}>
            <h2 id="options-exposure-heading">Options exposure</h2>
            <span className={styles.symbol}>{data.symbol}</span>
            {!data.complete ? <span className={styles.partial}>PARTIAL</span> : null}
          </div>
        </div>
        <div className={styles.telemetry}>
          <time dateTime={data.source_time}>{formatTimestamp(data.source_time)}</time>
          <span>SPOT {formatStrike(data.spot)}</span>
        </div>
      </header>

      <div className={styles.controls} data-testid="options-exposure-controls">
        <label className={styles.field}>
          <span>Metric</span>
          <select
            aria-label="Exposure metric"
            value={metric}
            onChange={(event) => setMetric(event.target.value as OptionsExposureMetric)}
          >
            {EXPOSURE_METRIC_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>{option.label}</option>
            ))}
          </select>
        </label>

        <label className={styles.field}>
          <span>Strike range</span>
          <select
            aria-label="Strike range"
            value={String(strikeWindow)}
            onChange={(event) => {
              const next = event.target.value;
              setStrikeWindow(next === "all" ? "all" : Number(next) as OptionsExposureStrikeWindow);
            }}
          >
            {EXPOSURE_STRIKE_OPTIONS.map((option) => (
              <option key={option.label} value={String(option.value)}>{option.label}</option>
            ))}
          </select>
        </label>

        <div className={styles.controlGroup} role="group" aria-label="Measurement frequency">
          <span>Frequency</span>
          <div className={styles.segmented}>
            {(["eod", "intraday"] as const).map((value) => (
              <button
                key={value}
                type="button"
                aria-pressed={frequency === value}
                onClick={() => setFrequency(value)}
              >
                {value === "eod" ? "EOD" : "Intraday"}
              </button>
            ))}
          </div>
        </div>

        <div className={styles.controlGroup} role="group" aria-label="Visible control levels">
          <span>Control levels {visibleLevels.size}/7</span>
          <div className={styles.levelToggles}>
            {EXPOSURE_LEVEL_OPTIONS.map((option) => (
              <button
                key={option.key}
                type="button"
                aria-label={`Toggle ${option.label} level`}
                aria-pressed={visibleLevels.has(option.key)}
                onClick={() => toggleLevel(option.key)}
              >
                {option.label}
              </button>
            ))}
          </div>
        </div>
      </div>

      <div className={styles.expirations} role="group" aria-label="Expiration filter">
        <button
          type="button"
          aria-pressed={expirationDates === null}
          onClick={() => setExpirationDates(null)}
        >
          All Expirations
        </button>
        {data.expirations.map((expiration) => (
          <button
            key={expiration.expiration_date}
            type="button"
            aria-pressed={Boolean(expirationDates?.has(expiration.expiration_date))}
            onClick={() => selectExpiration(expiration.expiration_date)}
          >
            {formatExpiration(expiration.expiration_date)} <span>{expiration.dte}d</span>
          </button>
        ))}
      </div>

      {rows.length === 0 ? (
        <div className={styles.tableEmpty} role="status">
          No strikes match the current measurement controls.
        </div>
      ) : (
        <div className={styles.tableWrap} data-testid="options-exposure-table-wrap">
          <table className={styles.table} aria-label={`${data.symbol} options exposure by strike`}>
            <colgroup>
              <col className={styles.strikeColumn} />
              <col className={styles.levelColumn} />
              <col className={styles.exposureColumn} />
              <col className={styles.oiColumn} />
            </colgroup>
            <thead>
              <tr>
                <th scope="col">Strike</th>
                <th scope="col">Levels</th>
                <th scope="col">{exposureMetricLabel(metric)}</th>
                <th scope="col">Put OI / Call OI</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => {
                const isSpot = row.strike === spotStrike;
                const sign = row.value > 0 ? "positive" : row.value < 0 ? "negative" : "zero";
                const exposureWidth = `${Math.min(50, Math.abs(row.value) / maxExposure * 50)}%`;
                const strikeLevels = levelsByStrike.get(row.strike) ?? [];
                const absoluteMetric = isAbsoluteExposureMetric(metric);

                return (
                  <tr
                    key={row.strike}
                    className={isSpot ? styles.spotRow : undefined}
                    data-testid={`exposure-row-${row.strike}`}
                    data-spot={isSpot ? "true" : "false"}
                  >
                    <th scope="row" className={styles.strikeCell}>
                      <span>{formatStrike(row.strike)}</span>
                      {isSpot ? <span className={styles.spotLabel}>SPOT</span> : null}
                    </th>
                    <td className={styles.levelCell}>
                      {strikeLevels.map((level) => (
                        <span
                          className={styles.levelMarker}
                          key={level.key}
                          title={`${level.label} at ${level.value == null ? "unavailable" : formatStrike(level.value)}`}
                        >
                          {level.label} {level.value == null ? "unavailable" : formatStrike(level.value)}
                        </span>
                      ))}
                    </td>
                    <td className={styles.exposureCell}>
                      <div className={styles.exposureTrack} aria-hidden="true">
                        <span className={styles.zeroLine} />
                        <span
                          className={`${styles.exposureBar} ${sign === "negative" ? styles.negativeBar : styles.positiveBar} ${absoluteMetric ? styles.absoluteBar : ""}`}
                          style={{ "--bar-width": exposureWidth } as WidthStyle}
                          data-testid={`exposure-bar-${row.strike}`}
                          data-sign={sign}
                        />
                      </div>
                      <span className={`${styles.exposureValue} ${styles[`${sign}Value`]}`} data-testid={`exposure-value-${row.strike}`}>
                        {formatValue(row.value, metric)}
                      </span>
                    </td>
                    <td className={styles.oiCell}>
                      <span className={styles.oiValue}>{compactNumber(row.putOi)}</span>
                      <div className={styles.oiTrack} aria-hidden="true">
                        <span
                          className={`${styles.oiBar} ${styles.putOiBar}`}
                          style={{ "--bar-width": `${row.putOi / maxOi * 50}%` } as WidthStyle}
                        />
                        <span
                          className={`${styles.oiBar} ${styles.callOiBar}`}
                          style={{ "--bar-width": `${row.callOi / maxOi * 50}%` } as WidthStyle}
                        />
                      </div>
                      <span className={styles.oiValue}>{compactNumber(row.callOi)}</span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
