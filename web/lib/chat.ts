import type { Dispatch, SetStateAction } from "react";
import type { ApiMessage, AssistantResponse, Message, PiResponse, WorkspaceSection } from "./types";
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

  if (lower.startsWith("analyze ")) {
    const tokenized = lower.replace(/^\s*analyze\s+/, "").trim().split(/\s+/)[0];
    if (tokenized) {
      return `/evaluate ${tokenized.toUpperCase()}`;
    }
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

  if (/\bscan\b/.test(lower)) {
    return `/scan`;
  }

  return null;
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

  const payload = (await response.json()) as AssistantResponse;

  if (!response.ok) {
    if (payload.error) {
      return `Error: ${payload.error}`;
    }
    return "Assistant service returned an error.";
  }

  if (typeof payload.content === "string" && payload.content.trim()) {
    return formatAssistantPayload(payload.content);
  }

  return fallbackReply(latestMessage);
}

export async function requestPiReply(command: string): Promise<string> {
  const response = await fetch("/api/pi", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ input: command }),
  });

  const payload = (await response.json()) as PiResponse;
  const normalized = normalizeTextLines(payload.output || "");
  const canonicalCommand = command.trim().replace(/^\//, "").split(/\s+/)[0] ?? "";

  if (!response.ok) {
    if (payload.error) {
      return `Error: ${payload.error}`;
    }
    return "PI command request failed.";
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

  if (pathname.startsWith("/journal")) {
    return "journal";
  }

  if (pathname.startsWith("/regime")) {
    return "regime";
  }

  if (pathname.startsWith("/internals")) {
    return "internals";
  }

  // Dynamic ticker route: /AAPL, /GOOG, etc. (1-5 alpha chars)
  if (/^\/[A-Za-z]{1,5}$/.test(pathname)) {
    return "ticker-detail";
  }

  return fallback;
}
