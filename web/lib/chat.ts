import type { Dispatch, SetStateAction } from "react";
import type {
  ApiMessage,
  AssistantOrderProposal,
  AssistantResponse,
  Message,
  PiResponse,
  WorkspaceSection,
} from "./types";
import { PI_COMMAND_ALIASES, PI_COMMAND_SET } from "./data";
import {
  createTimestamp,
  formatAssistantPayload,
  formatPiPayload,
  normalizeTextLines,
  sleep,
} from "./utils";

export function isPiCommandInput(raw: string) {
  const normalized = raw.trim().toLowerCase();
  const first = normalized.replace(/^\//, "").split(/\s+/)[0];
  return first ? PI_COMMAND_SET.has(first) : false;
}

export function normalizeCommandInput(raw: string) {
  const trimmed = raw.trim();
  return trimmed.startsWith("/") ? trimmed : `/${trimmed}`;
}

export function routeToPiPrompt(raw: string): string | null {
  const normalized = raw.trim();
  if (!normalized) {
    return null;
  }

  if (isPiCommandInput(normalized)) {
    return normalizeCommandInput(normalized);
  }

  const lower = normalized.toLowerCase();
  const alias = PI_COMMAND_ALIASES[lower];
  if (alias) {
    return alias;
  }

  const analyzeTicker = normalized.match(/^analyze\s+\$?([A-Za-z][A-Za-z0-9.-]{0,9})\s*$/);
  if (analyzeTicker) {
    return `/evaluate ${analyzeTicker[1].toUpperCase()}`;
  }

  if (/\bportfolio\b/.test(lower) || /\bpositions?\b/.test(lower)) {
    return "/portfolio";
  }

  if (/\bdiscover\b/.test(lower)) {
    return "/discover";
  }

  if (/\bjournal\b/.test(lower)) {
    return "/journal";
  }

  // Leap-scan MUST be matched before the generic `\bscan\b` branch — otherwise
  // "run a leap scan for mag7" is swallowed by the flow scanner. The LEAP
  // scanner requires a ticker group, so an unrecognized target falls back to
  // the mag7 megacap preset rather than erroring on a bare `/leap-scan`.
  if (/\bleap\b/.test(lower)) {
    return `/leap-scan --preset ${detectLeapPreset(lower)}`;
  }

  if (/\bscan\b/.test(lower)) {
    return `/scan`;
  }

  return null;
}

/**
 * Map natural-language phrasing to one of the LEAP scanner's whitelisted
 * presets (sectors, mag7, semis, emerging, china — see `executeLeapScan` in
 * `app/api/pi/route.ts`). Defaults to `mag7` (megacaps) when no group is named,
 * so the command always runs against a real ticker set.
 */
const LEAP_PRESET_PATTERNS: ReadonlyArray<readonly [RegExp, string]> = [
  [/\bsemi(?:conductor)?s?\b|\bchips?\b|\bsox\b/, "semis"],
  [/\bsectors?\b/, "sectors"],
  [/\bemerging\b|\bem\b/, "emerging"],
  [/\bchina\b|\bchinese\b/, "china"],
  [/\bmag\s?7\b|\bmagnificent\b/, "mag7"],
];

function detectLeapPreset(lower: string): string {
  for (const [pattern, preset] of LEAP_PRESET_PATTERNS) {
    if (pattern.test(lower)) {
      return preset;
    }
  }
  return "mag7";
}

export function fallbackReply(input: string) {
  const query = input.trim().toLowerCase();

  if (!query) {
    return "I can analyze flow structure, scan alignment, and risk, then map to a decision view.";
  }

  if (query.includes("analyze brze") || query.includes("brze")) {
    return "BRZE is against-flow. You are long 300x Mar 20 calls, and flow is negative with 29% distributed bias. If this continues near expiry, reduce risk or hedge immediately.";
  }

  if (query.includes("analyze rr") || query.includes(" rr")) {
    return "RR shows 36% distributed flow and a sustained signal. Keep a hard risk gate: no add, and ensure thesis still controls risk.";
  }

  if (query.includes("compare support vs against") || query.includes("support against") || query.includes("support vs against")) {
    return "Support side currently has 6 positions with confirmation; against side has 2 with a higher urgency profile. Treat against as active monitor tier.";
  }

  if (query.includes("action") || query.includes("items")) {
    return "Priority list: BRZE, RR, then MSFT. Confirm any additional prints before adding exposure.";
  }

  if (query.includes("watch list") || query.includes("watch closely")) {
    return "Watch list is flagged from mixed intraday flow. MSFT and BKD need one full session before any structural decision.";
  }

  if (query.includes("portfolio") || query.includes("positions")) {
    return "Portfolio snapshot: 19 positions total. 7 defined structure, 12 undefined. Net liquidation is $981,353. Flow-aligned positions currently lead.";
  }

  return "I can review any ticker, compare support/against groups, or walk through risk and Kelly logic for any position.";
}

async function readJsonBody<T>(response: Response): Promise<T | null> {
  const textReader = (response as { text?: () => Promise<string> }).text;
  if (typeof textReader === "function") {
    try {
      const raw = await textReader.call(response);
      if (!raw.trim()) {
        return null;
      }
      return JSON.parse(raw) as T;
    } catch {
      return null;
    }
  }

  try {
    return (await response.json()) as T;
  } catch {
    return null;
  }
}

export async function requestAssistantReply(history: ApiMessage[], latestMessage: string): Promise<string> {
  const response = await fetch("/api/assistant", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      messages: [
        ...history,
        { role: "user", content: latestMessage },
      ],
    }),
  });

  const payload = await readJsonBody<AssistantResponse>(response);

  if (!response.ok) {
    if (payload?.error) {
      return `Error: ${payload.error}`;
    }
    return "Assistant service returned an error.";
  }

  if (typeof payload?.content === "string" && payload.content.trim()) {
    return formatAssistantPayload(payload.content);
  }

  return fallbackReply(latestMessage);
}

export type AssistantTurn = {
  content: string;
  proposal: AssistantOrderProposal | null;
};

/**
 * Like {@link requestAssistantReply} but preserves the structured order
 * proposal (F7). The agentic loop returns a `place_order` proposal instead of
 * executing it; ChatPanel renders the proposal as a confirm card. The order is
 * NEVER executed here.
 */
export async function requestAssistantTurn(
  history: ApiMessage[],
  latestMessage: string,
): Promise<AssistantTurn> {
  const response = await fetch("/api/assistant", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      messages: [...history, { role: "user", content: latestMessage }],
    }),
  });

  const payload = await readJsonBody<AssistantResponse>(response);

  if (!response.ok) {
    const message = payload?.error ? `Error: ${payload.error}` : "Assistant service returned an error.";
    return { content: message, proposal: null };
  }

  const content =
    typeof payload?.content === "string" && payload.content.trim()
      ? formatAssistantPayload(payload.content)
      : fallbackReply(latestMessage);

  return { content, proposal: payload?.proposal ?? null };
}

/**
 * Executes a confirmed order proposal through the live placement path. Maps the
 * proposal's `place_order` input to the `/api/orders/place` body shape. Called
 * ONLY from the ChatPanel confirm card's Confirm action — never automatically.
 */
export async function placeProposedOrder(
  proposal: AssistantOrderProposal,
): Promise<{ ok: boolean; message: string }> {
  const input = proposal.input;
  const symbol = typeof input.ticker === "string" ? input.ticker.toUpperCase() : "";
  const action = typeof input.action === "string" ? input.action.toUpperCase() : "";
  const quantity = typeof input.quantity === "number" ? input.quantity : Number(input.quantity);
  const limitPrice = typeof input.limit_price === "number" ? input.limit_price : Number(input.limit_price);

  const body: Record<string, unknown> = {
    type: "stock",
    symbol,
    action,
    quantity,
    tif: "DAY",
  };
  if (Number.isFinite(limitPrice) && limitPrice > 0) {
    body.limitPrice = limitPrice;
  }

  const response = await fetch("/api/orders/place", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const json = await response.json().catch(() => ({}));
  if (!response.ok) {
    return { ok: false, message: (json as { error?: string }).error || "Order placement failed." };
  }
  return { ok: true, message: (json as { message?: string }).message || `Order placed: ${proposal.summary}` };
}

export async function requestPiReply(command: string): Promise<string> {
  const response = await fetch("/api/pi", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ input: command }),
  });

  const payload = await readJsonBody<PiResponse>(response);
  const normalized = normalizeTextLines(payload?.output || "");
  const canonicalCommand = command.trim().replace(/^\//, "").split(/\s+/)[0] ?? "";

  if (!response.ok) {
    if (payload?.error) {
      return `Error: ${payload.error}`;
    }
    return "PI command request failed.";
  }

  if (!payload) {
    return "No output returned from PI command.";
  }

  if (payload.status === "error") {
    const details = payload.stderr ? `\n\nDetails:\n${payload.stderr}` : "";
    return `Command '${payload.command}' failed: ${normalized}${details}`;
  }

  if (!normalized) {
    return "No output returned from PI command.";
  }

  return formatPiPayload(canonicalCommand, normalized);
}

export async function streamMessage(messageId: string, fullText: string, setMessages: Dispatch<SetStateAction<Message[]>>) {
  const chunk = 120;
  let rendered = "";
  const source = fullText.length ? fullText : "No output returned from PI command.";
  const parts = source.match(new RegExp(`.{1,${chunk}}`, "gs"));

  if (!parts) {
    setMessages((current) =>
      current.map((message) => (message.id === messageId ? { ...message, content: source } : message)),
    );
    return;
  }

  for (const piece of parts) {
    rendered += piece;
    setMessages((current) => current.map((message) => (message.id === messageId ? { ...message, content: rendered } : message)));
    await sleep(8);
  }
}

export function resolveSectionFromPath(pathname: string | null, fallback: WorkspaceSection): WorkspaceSection {
  if (!pathname) {
    return fallback;
  }

  if (pathname === "/" || pathname === "/dashboard") {
    return "dashboard";
  }

  if (pathname.startsWith("/flow-analysis")) {
    return "flow-analysis";
  }

  if (pathname.startsWith("/options")) {
    return "options";
  }

  if (pathname.startsWith("/portfolio")) {
    return "portfolio";
  }

  if (pathname.startsWith("/performance")) {
    return "performance";
  }

  if (pathname.startsWith("/orders")) {
    return "orders";
  }

  if (pathname.startsWith("/scanner")) {
    return "scanner";
  }

  if (pathname.startsWith("/discover")) {
    return "discover";
  }

  if (pathname.startsWith("/watchlist")) {
    return "watchlist";
  }

  if (pathname.startsWith("/journal")) {
    return "journal";
  }

  if (pathname.startsWith("/regime")) {
    return "regime";
  }

  if (pathname.startsWith("/alerts")) {
    return "alerts";
  }

  if (pathname.startsWith("/workflow")) {
    return "workflow";
  }

  if (pathname.startsWith("/admin")) {
    return "admin";
  }

  if (pathname.startsWith("/profile")) {
    return "profile";
  }

  // Dynamic ticker route: /AAPL, /GOOG, etc. (1-5 alpha chars)
  if (/^\/[A-Za-z]{1,5}$/.test(pathname)) {
    return "ticker-detail";
  }

  return fallback;
}
