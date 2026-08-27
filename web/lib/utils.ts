import type { JsonValue } from "./types";

export function createTimestamp() {
  return new Date().toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit", hour12: false });
}

export function titleCase(input: string) {
  return input
    .replace(/[_-]+/g, " ")
    .split(" ")
    .filter(Boolean)
    .map((segment) => segment.charAt(0).toUpperCase() + segment.slice(1))
    .join(" ");
}

export function formatCurrency(value: unknown) {
  const amount = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(amount)) {
    return "N/A";
  }

  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 0,
  }).format(amount);
}

export function parsePossibleJson(raw: string): unknown | null {
  const trimmed = raw.trim();
  if (!trimmed) {
    return null;
  }

  if (!/^[\[{].*[\]}]$/s.test(trimmed)) {
    return null;
  }

  try {
    return JSON.parse(trimmed) as unknown;
  } catch {
    return null;
  }
}

export function normalizeForCell(raw: unknown) {
  if (raw === null || raw === undefined) {
    return "N/A";
  }
  if (typeof raw === "string") {
    return raw;
  }
  if (typeof raw === "number" || typeof raw === "boolean") {
    return String(raw);
  }
  try {
    return JSON.stringify(raw);
  } catch {
    return String(raw);
  }
}

export function normalizeTextLines(raw: string) {
  return raw
    .split("\n")
    .map((line) => line.trimEnd())
    .join("\n")
    .trim();
}

export function valueToText(value: unknown): string {
  if (value === null || value === undefined) {
    return "N/A";
  }
  if (typeof value === "boolean" || typeof value === "number") {
    return String(value);
  }
  if (typeof value === "string") {
    return value;
  }

  return "";
}

export function sleep(ms: number) {
  return new Promise((resolve) => {
    setTimeout(resolve, ms);
  });
}

export function formatArrayAsTable(parsed: unknown[]) {
  if (!parsed.length) {
    return "No rows available.";
  }

  if (!parsed.every((row) => row && typeof row === "object" && !Array.isArray(row))) {
    return null;
  }

  const rows = parsed as Record<string, JsonValue>[];
  const columns = Array.from(
    rows.reduce((acc, row) => {
      Object.keys(row).forEach((key) => {
        acc.add(key);
      });
      return acc;
    }, new Set<string>()),
  );

  if (!columns.length) {
    return null;
  }

  const header = columns.map((column) => titleCase(column));
  const separator = columns.map(() => "---");
  const body = rows.map((row) => columns.map((column) => normalizeForCell(row[column])));

  const table = [`| ${header.join(" | ")} |`, `| ${separator.join(" | ")} |`, ...body.map((row) => `| ${row.join(" | ")} |`)];

  return table.join("\n");
}

export function extractSingleArrayFromObject(parsed: Record<string, unknown>) {
  const rowsEntries = Object.entries(parsed).filter(([, value]) => Array.isArray(value));
  if (rowsEntries.length !== 1) {
    return null;
  }

  const [field, value] = rowsEntries[0] as [string, unknown[]];
  if (!value.length || !value.every((row) => row && typeof row === "object" && !Array.isArray(row))) {
    return null;
  }

  const table = formatArrayAsTable(value);
  if (!table) {
    return null;
  }

  return `${field.toUpperCase()}:\n${table}`;
}

export function formatJsonObject(value: Record<string, JsonValue>, indent = 0): string {
  const spaces = " ".repeat(indent * 2);
  const lines: string[] = [];
  for (const [key, entry] of Object.entries(value)) {
    const label = titleCase(key);
    if (entry === null || typeof entry !== "object") {
      const stringValue = valueToText(entry);
      if (stringValue) {
        lines.push(`${spaces}${label}: ${stringValue}`);
      }
      continue;
    }

    if (Array.isArray(entry)) {
      lines.push(`${spaces}${label}:`);
      if (!entry.length) {
        lines.push(`${spaces}  - none`);
        continue;
      }
      for (const [index, row] of entry.entries()) {
        if (row === null || typeof row !== "object") {
          lines.push(`${spaces}  ${index + 1}. ${valueToText(row)}`);
          continue;
        }
        const rowLabel = Object.entries(row as Record<string, JsonValue>)
          .filter(([, rowValue]) => rowValue !== undefined)
          .map(([rowKey, rowValue]) => `${rowKey}: ${valueToText(rowValue)}`)
          .join(" | ");
        lines.push(`${spaces}  ${index + 1}. ${rowLabel || "(empty row)"}`);
      }
      continue;
    }

    const nested = formatJsonObject(entry as Record<string, JsonValue>, indent + 1);
    lines.push(`${spaces}${label}:`);
    if (nested) {
      lines.push(nested);
    }
  }

  return lines.join("\n");
}

export function formatPortfolioPayload(raw: unknown): string {
  const payload = raw as { bankroll?: unknown; position_count?: unknown; defined_risk_count?: unknown; undefined_risk_count?: unknown; last_sync?: unknown; positions?: unknown[] };
  const positions = Array.isArray(payload?.positions) ? payload.positions : [];

  const lines = [
    "Portfolio Snapshot",
    `Bankroll: ${formatCurrency(payload?.bankroll)}`,
    `Positions: ${Number(payload?.position_count ?? positions.length)}`,
    `Defined Risk: ${Number(payload?.defined_risk_count ?? 0)}`,
    `Undefined Risk: ${Number(payload?.undefined_risk_count ?? 0)}`,
    `Last Sync: ${String(payload?.last_sync ?? "N/A")}`,
    "",
    "Positions:",
  ];

  if (!positions.length) {
    lines.push("No positions found.");
    return lines.join("\n");
  }

  const table = formatArrayAsTable(positions);
  if (table && table !== "No rows available.") {
    lines.push(table);
    return lines.join("\n");
  }

  for (const [index, position] of positions.entries()) {
    if (typeof position !== "object" || position === null) {
      lines.push(`${index + 1}. ${String(position)}`);
      continue;
    }

    const entry = position as {
      ticker?: string;
      structure?: string;
      expiry?: string;
      risk_profile?: string;
      entry_cost?: number;
    };

    lines.push(
      `${index + 1}. ${entry.ticker ?? "UNKNOWN"}: ${entry.structure ?? "No structure"} (` +
        `expiry: ${entry.expiry ?? "N/A"}, entry cost: ${formatCurrency(entry.entry_cost)})`,
    );
    if (entry.risk_profile) {
      lines.push(`   Risk Profile: ${entry.risk_profile}`);
    }
  }

  return lines.join("\n");
}

export function formatJournalPayload(raw: unknown): string {
  const payload = raw as { trades?: unknown[] };
  const trades = Array.isArray(payload?.trades) ? payload.trades : [];

  const lines = ["Recent Journal", ""];
  if (!trades.length) {
    lines.push("No trades logged.");
    return lines.join("\n");
  }

  const table = formatArrayAsTable(trades);
  if (table && table !== "No rows available.") {
    lines.push(table);
    return lines.join("\n");
  }

  for (const [index, trade] of trades.entries()) {
    if (typeof trade !== "object" || trade === null) {
      lines.push(`${index + 1}. ${String(trade)}`);
      continue;
    }

    const entry = trade as { timestamp?: string; ticker?: string; decision?: string; confidence?: string | number; note?: string };
    const prefix = `${index + 1}. ${entry.timestamp ?? "No timestamp"} ${entry.ticker ?? "N/A"} ${entry.decision ?? ""}`.trim();
    lines.push(prefix);
    if (entry.confidence !== undefined) {
      lines.push(`   Confidence: ${entry.confidence}`);
    }
    if (entry.note) {
      lines.push(`   Note: ${entry.note}`);
    }
  }

  return lines.join("\n");
}

export function formatGenericPayload(parsedPayload: unknown): string {
  if (Array.isArray(parsedPayload)) {
    const table = formatArrayAsTable(parsedPayload);
    if (table) {
      return normalizeTextLines(table);
    }
    return normalizeTextLines(formatJsonObject({ list: parsedPayload }, 0).replace(/^List:\n/, ""));
  }

  if (parsedPayload && typeof parsedPayload === "object") {
    const singleArray = extractSingleArrayFromObject(parsedPayload as Record<string, unknown>);
    if (singleArray) {
      return normalizeTextLines(singleArray);
    }
    return normalizeTextLines(formatJsonObject(parsedPayload as Record<string, JsonValue>, 0));
  }

  return normalizeTextLines(String(parsedPayload));
}

export function formatAssistantPayload(output: string): string {
  const parsed = parsePossibleJson(output);
  if (!parsed) {
    return normalizeTextLines(output);
  }

  return formatGenericPayload(parsed);
}

export function formatPiPayload(command: string, output: string): string {
  const parsed = parsePossibleJson(output);
  if (!parsed) {
    return normalizeTextLines(output);
  }

  const canonical = command.replace(/^\//, "").toLowerCase();

  if (canonical === "portfolio") {
    return normalizeTextLines(formatPortfolioPayload(parsed));
  }

  if (canonical === "journal") {
    return normalizeTextLines(formatJournalPayload(parsed));
  }

  return formatGenericPayload(parsed);
}
