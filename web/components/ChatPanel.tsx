"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ArrowDown, Copy, Check } from "lucide-react";
import { ApprovalGate, AskComposer, EngineTrace } from "@/components/agent";
import { buildTurnSteps, describeEngines } from "@/lib/agent/turnSteps";
import type {
  ApiMessage,
  AssistantOrderInput,
  AssistantOrderProposal,
  AssistantToolEvent,
  Message,
  PortfolioData,
  WorkspaceSection,
} from "@/lib/types";
import { quickPromptsBySection } from "@/lib/data";
import { createTimestamp } from "@/lib/utils";
import {
  fallbackReply,
  placeProposedOrder,
  requestAssistantTurn,
  requestPiReply,
  routeToPiPrompt,
  streamMessage,
} from "@/lib/chat";
import MarkdownRenderer from "./MarkdownRenderer";
import { OrderQuoteTelemetry } from "@/components/QuoteTelemetry";
import { OrderRiskGate } from "@/lib/order/risk/OrderRiskGate";
import type { OrderRiskInput, OrderRiskState } from "@/lib/order/risk/useOrderRisk";
import { computeNetOptionQuote, formatExpiry, type OrderLeg } from "@/lib/optionsChainUtils";
import { optionKey, type PriceData } from "@/lib/pricesProtocol";
import {
  buildQuoteTelemetryModel,
  comboQuotePriceData,
  type QuoteTelemetryModel,
} from "@/lib/quoteTelemetry";

type ChatPanelProps = {
  activeSection: WorkspaceSection;
  portfolio?: PortfolioData | null;
  /**
   * Overlay open flag, forwarded to AskComposer's `focusKey` so the composer
   * takes focus on every ⌘J open (replaces the old composerRef focus effect).
   */
  isOpen?: boolean;
  /**
   * A prompt handed over from another surface (e.g. a newsfeed follow-up chip).
   * Sent once on arrival, then reported back via onSeedConsumed.
   */
  seedPrompt?: string | null;
  onSeedConsumed?: () => void;
  /**
   * Live quotes, keyed the way `usePrices` keys them (ticker for a stock,
   * `optionKey()` for a contract). The proposal gate reads the one instrument
   * it is about to route out of this map.
   */
  prices?: Record<string, PriceData>;
};

const NO_PRICES: Record<string, PriceData> = {};

/**
 * Request lifecycle as a named union, not scattered booleans. Each state maps
 * to exactly one visual treatment: `submitted` → typing dots, `streaming` →
 * tokens + cursor, `done`/`error` → settled bubble. Kills the "No output."
 * flash that the old `isBusy` boolean produced.
 */
type ChatStatus = "idle" | "submitted" | "streaming" | "done" | "error";

const STICK_THRESHOLD_PX = 80;

function CopyButton({ content }: { content: string }) {
  const [copied, setCopied] = useState(false);
  const onCopy = useCallback(() => {
    void navigator.clipboard?.writeText(content).then(() => {
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    });
  }, [content]);
  return (
    <button type="button" className="chat-action-btn" onClick={onCopy} aria-label="Copy message">
      {copied ? <Check size={11} /> : <Copy size={11} />}
      {copied ? "Copied" : "Copy"}
    </button>
  );
}

function proposalRiskInput(proposal: AssistantOrderProposal | null): OrderRiskInput | null {
  if (!proposal) return null;
  const input = proposal.input;
  if (input.type === "stock") {
    return {
      type: "linear", ticker: input.ticker, action: input.action,
      quantity: input.quantity, limitPrice: input.limit_price, multiplier: 1,
      instrument: "stock", description: proposal.summary,
    };
  }
  const signedPremium = input.action === "BUY" ? input.limit_price : -input.limit_price;
  const chainLegs =
    input.type === "combo"
      ? input.legs.map((leg) => ({
          action: leg.action,
          right: leg.right,
          strike: leg.strike,
          expiry: leg.expiry,
          quantity: input.quantity * leg.ratio,
        }))
      : [{ action: input.action, right: input.right, strike: input.strike, expiry: input.expiry, quantity: input.quantity }];
  return {
    type: "options", ticker: input.ticker, netPremium: signedPremium,
    description: proposal.summary, totalCost: signedPremium * input.quantity * 100,
    chainLegs,
  };
}

/** Names the instrument the way its ticket names it, e.g. "MU 2026-09-18 $120 C". */
function proposalQuoteLabel(input: AssistantOrderInput): string {
  if (input.type === "option") {
    return `${input.ticker} ${formatExpiry(input.expiry)} $${input.strike} ${input.right}`;
  }
  if (input.type === "combo") {
    return `${input.ticker} ${input.structure ?? "Combo"}`;
  }
  return input.ticker;
}

/**
 * A BAG is not a quoted instrument: net the legs with the shared combo
 * calculation, then hand that net quote to the SAME model builder every
 * single-leg surface uses.
 */
function comboQuoteModel(
  input: Extract<AssistantOrderInput, { type: "combo" }>,
  prices: Record<string, PriceData>,
): QuoteTelemetryModel | null {
  const legs: OrderLeg[] = input.legs.map((leg, index) => ({
    id: `${input.ticker}-${index}`,
    action: leg.action,
    right: leg.right,
    strike: leg.strike,
    expiry: leg.expiry,
    quantity: leg.ratio,
    limitPrice: null,
  }));
  const net = computeNetOptionQuote(legs, prices, input.ticker);
  if (net.bid == null && net.ask == null) return null;
  return buildQuoteTelemetryModel(
    comboQuotePriceData({
      symbol: input.ticker,
      bid: net.bid,
      ask: net.ask,
      last: net.mid,
      timestamp: net.timestamp,
    }),
  );
}

type ProposalQuote = {
  label: string;
  priceData: PriceData | null;
  model: QuoteTelemetryModel | null;
};

/**
 * The proposal's own quote: the underlying for a stock, the contract for a
 * single-leg option, the net market for a combo.
 */
function proposalQuote(
  proposal: AssistantOrderProposal | null,
  prices: Record<string, PriceData>,
): ProposalQuote | null {
  if (!proposal) return null;
  const input = proposal.input;
  const label = proposalQuoteLabel(input);
  if (input.type === "combo") {
    return { label, priceData: null, model: comboQuoteModel(input, prices) };
  }
  const key =
    input.type === "option"
      ? optionKey({
          symbol: input.ticker,
          expiry: input.expiry,
          strike: input.strike,
          right: input.right,
        })
      : input.ticker;
  return { label, priceData: prices[key] ?? null, model: null };
}

export default function ChatPanel({
  activeSection,
  portfolio,
  isOpen = true,
  seedPrompt = null,
  onSeedConsumed,
  prices = NO_PRICES,
}: ChatPanelProps) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [status, setStatus] = useState<ChatStatus>("idle");
  const [lastError, setLastError] = useState("");
  const [proposal, setProposal] = useState<AssistantOrderProposal | null>(null);
  // Tool telemetry for the turn in flight. Reset per send so a finished turn's
  // trace can't leak into the next one.
  const [turnTools, setTurnTools] = useState<AssistantToolEvent[]>([]);
  const [turnModel, setTurnModel] = useState<string | null>(null);
  const [isPlacing, setPlacing] = useState(false);
  const [riskState, setRiskState] = useState<OrderRiskState | null>(null);
  const riskInput = useMemo(() => proposalRiskInput(proposal), [proposal]);
  const quote = useMemo(() => proposalQuote(proposal, prices), [proposal, prices]);
  const [showJump, setShowJump] = useState(false);

  const messagesRef = useRef<HTMLDivElement | null>(null);
  const atBottomRef = useRef(true);

  const sectionPrompts = quickPromptsBySection[activeSection];
  const isBusy = status === "submitted" || status === "streaming";

  // Stick-to-bottom: only auto-scroll while the user is already pinned to the
  // bottom. Reading layout in an effect keyed on messages keeps the hot path
  // out of React state — streamMessage mutates content many times per turn.
  const scrollToBottom = useCallback(() => {
    const el = messagesRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, []);

  useEffect(() => {
    if (atBottomRef.current) scrollToBottom();
  }, [messages, scrollToBottom]);

  const onTranscriptScroll = useCallback(() => {
    const el = messagesRef.current;
    if (!el) return;
    const gap = el.scrollHeight - el.scrollTop - el.clientHeight;
    const atBottom = gap < STICK_THRESHOLD_PX;
    atBottomRef.current = atBottom;
    setShowJump(!atBottom);
  }, []);

  const jumpToBottom = useCallback(() => {
    atBottomRef.current = true;
    setShowJump(false);
    scrollToBottom();
  }, [scrollToBottom]);

  const sendMessage = async (prompt: string) => {
    const cleaned = prompt.trim();
    if (!cleaned || isBusy) {
      return;
    }
    // A new turn invalidates any prior model-controlled destructive intent.
    setProposal(null);
    setRiskState(null);

    const userMessage: Message = {
      id: `u-${Date.now()}`,
      role: "user",
      timestamp: createTimestamp(),
      content: cleaned,
    };

    const conversation: ApiMessage[] = messages.map((message) => ({
      role: message.role,
      content: message.content,
    }));

    // A new turn always re-pins to the bottom so a prior scroll-up can't wedge
    // auto-scroll off for the rest of the session.
    atBottomRef.current = true;
    setShowJump(false);

    const assistantId = `a-${Date.now()}`;
    const assistantMessage: Message = {
      id: assistantId,
      role: "assistant",
      timestamp: createTimestamp(),
      content: "",
    };

    setMessages((current) => [...current, userMessage, assistantMessage]);
    setStatus("submitted");
    setLastError("");
    setTurnTools([]);
    setTurnModel(null);

    try {
      const piCommand = routeToPiPrompt(cleaned);
      if (piCommand) {
        const assistantContent = await requestPiReply(piCommand);
        setStatus("streaming");
        await streamMessage(assistantId, assistantContent, setMessages);
      } else {
        const turn = await requestAssistantTurn(conversation, cleaned);
        setTurnTools(turn.toolEvents);
        setTurnModel(turn.model);
        setStatus("streaming");
        await streamMessage(assistantId, turn.content, setMessages);
        // F7: never auto-execute. A destructive order proposal is surfaced as
        // a confirm card the operator must explicitly accept.
        if (turn.proposal) {
          setProposal(turn.proposal);
        }
      }
      setStatus("done");
    } catch (error) {
      const isPiCommand = Boolean(routeToPiPrompt(cleaned));
      const fallback = isPiCommand ? "PI command failed to run in this session." : fallbackReply(cleaned);
      const errorMessage =
        error instanceof Error
          ? error.message
          : isPiCommand
            ? "Unexpected PI command error."
            : "Unexpected assistant error.";
      const fallbackContent = `${fallback}\n\nFallback note: ${errorMessage}`;

      setMessages((current) =>
        current.map((message) =>
          message.id === assistantId ? { ...message, content: fallbackContent } : message,
        ),
      );
      setLastError(errorMessage);
      setStatus("error");
    }
  };

  // A handed-over prompt sends itself once on arrival. Both callbacks are read
  // through refs so an inline parent arrow (new identity every render) can't
  // turn this into a send loop; the effect keys on the prompt alone.
  const sendMessageRef = useRef(sendMessage);
  sendMessageRef.current = sendMessage;
  const onSeedConsumedRef = useRef(onSeedConsumed);
  onSeedConsumedRef.current = onSeedConsumed;

  useEffect(() => {
    if (!seedPrompt) return;
    void sendMessageRef.current(seedPrompt);
    onSeedConsumedRef.current?.();
  }, [seedPrompt]);

  const confirmProposal = async () => {
    if (!proposal || isPlacing || !riskState?.okToSubmit) return;
    setPlacing(true);
    setLastError("");
    try {
      const result = await placeProposedOrder(proposal);
      setMessages((current) => [
        ...current,
        {
          id: `a-${Date.now()}-order`,
          role: "assistant",
          timestamp: createTimestamp(),
          content: result.ok ? result.message : `Order failed: ${result.message}`,
        },
      ]);
      if (!result.ok) setLastError(result.message);
      setProposal(null);
    } catch (error) {
      const message = error instanceof Error ? error.message : "Order placement failed.";
      setLastError(message);
    } finally {
      setPlacing(false);
    }
  };

  const cancelProposal = () => {
    if (isPlacing) return;
    setProposal(null);
  };

  const lastAssistantId = [...messages].reverse().find((m) => m.role === "assistant")?.id ?? null;

  return (
    <div className="chat-panel">
      {/* The launcher head is the overlay's single header — ChatPanel no
          longer renders its own, so the surface reads as one conversation. */}
      <div className="chat-shell">
          <div className="chat-transcript-wrap">
            {messages.length === 0 ? (
              <div className="chat-empty-state">
                <div className="chat-empty-state__title">Ask Radon</div>
                <p className="chat-empty-state__copy">
                  Flow analysis, scans, risk checks and journal queries. Type a request or pick one.
                </p>
                <div className="chat-empty-state__cards">
                  {sectionPrompts.map((prompt) => (
                    <button
                      type="button"
                      key={prompt}
                      className="chat-empty-card"
                      onClick={() => sendMessage(prompt)}
                    >
                      <span className="chat-empty-card__slash">/</span>
                      {prompt}
                    </button>
                  ))}
                </div>
              </div>
            ) : (
              <div
                ref={messagesRef}
                className="chat-messages"
                role="log"
                aria-live="polite"
                aria-atomic="false"
                aria-busy={status === "streaming" || status === "submitted"}
                onScroll={onTranscriptScroll}
              >
                {messages.map((message) => {
                  const isAssistant = message.role === "assistant";
                  const isPending = isAssistant && !message.content;
                  const isStreamingThis =
                    isAssistant && message.id === lastAssistantId && status === "streaming";
                  const canCopy = isAssistant && message.content && !isStreamingThis;
                  return (
                    <div
                      key={message.id}
                      className={`chat-message ${message.role}${isStreamingThis ? " streaming" : ""}`}
                    >
                      <div className="chat-meta">
                        <span className="chat-role">{isAssistant ? "Grok" : "You"}</span>
                        <span className="chat-time">{message.timestamp}</span>
                      </div>
                      <div className="chat-message-body">
                        {isPending ? (
                          <EngineTrace
                            steps={buildTurnSteps(turnTools, status === "error" ? "error" : "submitted")}
                            engines={describeEngines(turnModel)}
                          />
                        ) : (
                          <>
                            <MarkdownRenderer content={message.content} />
                            {isStreamingThis ? (
                              <span className="chat-cursor" aria-hidden="true" />
                            ) : null}
                          </>
                        )}
                      </div>
                      {canCopy ? (
                        <div className="chat-actions">
                          <CopyButton content={message.content} />
                        </div>
                      ) : null}
                    </div>
                  );
                })}
              </div>
            )}

            <button
              type="button"
              className="chat-jump-btn"
              data-hidden={!showJump}
              onClick={jumpToBottom}
              aria-label="Scroll to latest"
              tabIndex={showJump ? 0 : -1}
            >
              <ArrowDown size={11} />
              Latest
            </button>
          </div>

          {lastError ? <div className="chat-error">{lastError}</div> : null}

          {/* F7: never auto-execute. TODO(agent-ui): when the assistant returns
              sized alternatives (split clips, hold), map them into `options` and
              pass the confirmed option id through to placeProposedOrder. */}
          {proposal ? (
            <>
              {/* The order-risk chokepoint stays mandatory: the gate renders the
                  risk verdict and confirmProposal refuses unless okToSubmit. */}
              <OrderRiskGate
                input={riskInput}
                portfolio={portfolio}
                surface="assistant-chat"
                onState={setRiskState}
              />
              <ApprovalGate
                title="Confirmation required"
                body={proposal.summary}
                quote={
                  quote ? (
                    <OrderQuoteTelemetry
                      priceData={quote.priceData}
                      model={quote.model}
                      label={quote.label}
                      density="tight"
                    />
                  ) : null
                }
                options={[{ id: "route", label: "Route as proposed", meta: "AS PROPOSED" }]}
                busy={isPlacing}
                confirmDisabled={riskState?.okToSubmit !== true}
                onConfirm={() => void confirmProposal()}
                onDismiss={cancelProposal}
              />
            </>
          ) : null}

          <div className="chat-composer">
            {messages.length ? (
              <div className="chat-pills">
                {sectionPrompts.map((prompt) => (
                  <button
                    type="button"
                    key={prompt}
                    onClick={() => sendMessage(prompt)}
                    className="pill-chip"
                  >
                    / {prompt}
                  </button>
                ))}
              </div>
            ) : null}
            <AskComposer
              busy={isBusy}
              focusKey={isOpen}
              onSubmit={(text) => void sendMessage(text)}
            />
          </div>
        </div>
    </div>
  );
}
