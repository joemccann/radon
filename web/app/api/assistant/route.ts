import { NextRequest, NextResponse } from "next/server";

import { runAssistantLoop, type AssistantTurn } from "@/lib/assistant/loop";
import { enforceDemoAiQuota } from "@/lib/demo/enforceAiQuota";

type ChatRole = "user" | "assistant";

export type ChatMessage = {
  role: ChatRole;
  content: string;
};

export type AssistantPayload = {
  messages: ChatMessage[];
};

const SYSTEM_PROMPT =
  "You are Grok, running as Radon's trading operations assistant. " +
  "You analyze institutional flow, portfolio risk, and trade structure with the same direct style as Grok. " +
  "You can call tools to pull live flow, scans, gamma exposure, and the portfolio. " +
  "Destructive actions (placing orders) are never executed automatically: propose them and let the operator confirm. " +
  "Always respond in short, decisive blocks using signal, structure, kelly logic, and final decision. " +
  "If confidence is low, explicitly state uncertainty and recommend the next command or additional data.";

const isMockMode = () =>
  process.env.ASSISTANT_MOCK === "1" ||
  (process.env.NODE_ENV === "test" && process.env.ASSISTANT_MOCK !== "0");

function cleanString(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

function safeMessages(rawMessages: unknown): ChatMessage[] {
  if (!Array.isArray(rawMessages)) return [];

  const parsed: ChatMessage[] = [];
  for (const item of rawMessages) {
    const role = (item as { role?: unknown }).role;
    const content = (item as { content?: unknown }).content;
    if ((role === "user" || role === "assistant") && typeof content === "string" && content.trim()) {
      parsed.push({ role, content: content.trim() });
    }
  }
  return parsed;
}

function fallbackReply(input: string): string {
  const q = cleanString(input).toLowerCase();
  if (!q) return "Awaiting your instruction. Ask for ticker analysis, a scan review, or risk checks.";
  if (q.includes("help")) {
    return "Try: analyze [TICKER], compare support vs against, action items, watch list, or run scan/portfolio workflows.";
  }
  if (q.includes("brze")) {
    return "BRZE is currently against-flow. It is a near-expiry long call structure with distribution-heavy prints; this is higher urgency than neutral flow mismatches.";
  }
  if (q.includes("analyze rr") || q === "rr") {
    return "RR is against flow and currently shows sustained distribution, so position risk is elevated unless thesis data sharpens.";
  }
  return "I have flow context loaded for the dashboard and can expand on any ticker, structure, or command-style request.";
}

// The provider's offline mock prefixes completions with "Mock <provider>".
// When the loop runs under ASSISTANT_MOCK with the real (unmocked) provider, we
// rewrite that placeholder into the assistant's deterministic fallback so the
// dashboard mock stays useful and stable.
function isProviderMockContent(content: string): boolean {
  return /^Mock (anthropic|openai|gemini|xai|grok) response:/i.test(content);
}

function toTurns(messages: ChatMessage[]): AssistantTurn[] {
  return messages.map((message) => ({ role: message.role, content: message.content }));
}

function lastUserContent(messages: ChatMessage[]): string {
  return [...messages].reverse().find((message) => message.role === "user")?.content ?? "";
}

function bearerToken(request: NextRequest): string | undefined {
  const header = request.headers.get("authorization") ?? "";
  const match = header.match(/^Bearer\s+(.+)$/i);
  return match ? match[1] : undefined;
}

export async function POST(request: NextRequest): Promise<Response> {
  const mock = isMockMode();

  let body: AssistantPayload | null = null;
  try {
    body = (await request.json()) as AssistantPayload;
  } catch {
    if (mock) return NextResponse.json({ error: "No messages supplied." }, { status: 400 });
    return NextResponse.json({ error: "Invalid JSON payload." }, { status: 400 });
  }

  const messages = safeMessages(body?.messages);
  if (!messages.length) {
    return NextResponse.json({ error: "No messages supplied." }, { status: 400 });
  }

  const lastMessage = messages[messages.length - 1];
  if (!mock && lastMessage.role !== "user") {
    return NextResponse.json({ error: "The last message must be from user." }, { status: 400 });
  }

  // Demo AI quota (Phase 4) — no-op for non-demo users; 429 once a trial user
  // exhausts the daily assistant budget, BEFORE the expensive LLM loop runs.
  const quota = await enforceDemoAiQuota("assistant");
  if (quota) return quota;

  try {
    const result = await runAssistantLoop(toTurns(messages), SYSTEM_PROMPT, bearerToken(request));

    const content =
      mock && !result.proposal && isProviderMockContent(result.content)
        ? `Mock Grok response: ${fallbackReply(lastUserContent(messages))}`
        : result.content;

    return NextResponse.json({
      content,
      model: result.model,
      toolEvents: result.toolEvents,
      proposal: result.proposal ?? null,
      rounds: result.rounds,
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : "Unknown assistant error.";
    return NextResponse.json({ error: message }, { status: 502 });
  }
}
