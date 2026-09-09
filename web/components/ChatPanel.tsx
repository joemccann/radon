"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ArrowDown, ArrowUpRight, Copy, Check, Plus, X, RotateCcw, Pencil, Activity } from "lucide-react";
import { ApprovalGate, AskComposer, EngineTrace } from "@/components/agent";
import { buildTurnSteps, describeEngines } from "@/lib/agent/turnSteps";
import { assistantErrorMessage } from "@/lib/assistant/errorCopy";
import type {
  ApiMessage,
  AssistantOrderInput,
  AssistantOrderProposal,
  AssistantToolEvent,
  ChatImageAttachment,
  Message,
  PortfolioData,
  WorkspaceSection,
} from "@/lib/types";
import { createTimestamp } from "@/lib/utils";
import {
  buildUserMessage,
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
  /** Workspace context is displayed as location, not claimed as loaded data. */
  activeSection: WorkspaceSection;
  portfolio?: PortfolioData | null;
  /**
   * Overlay open flag, forwarded to AskComposer's `focusKey` so the composer
   * takes focus on every ⌘J open (replaces the old composerRef focus effect).
   */
  isOpen?: boolean;
  onClose?: () => void;
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
type TurnEvidence = { tools: AssistantToolEvent[]; model: string | null; failed?: boolean; stopped?: boolean };

const STICK_THRESHOLD_PX = 80;

function CopyButton({ content }: { content: string }) {
  const [copied, setCopied] = useState(false);
  const [copyFailed, setCopyFailed] = useState(false);
  const onCopy = useCallback(() => {
    if (!navigator.clipboard) { setCopyFailed(true); return; }
    void navigator.clipboard.writeText(content).then(() => {
      setCopyFailed(false);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    }).catch(() => setCopyFailed(true));
  }, [content]);
  return (
    <button type="button" className="chat-action-btn" onClick={onCopy} aria-label="Copy message">
      {copied ? <Check size={11} /> : <Copy size={11} />}
      {copyFailed ? "Select text to copy" : copied ? "Copied" : "Copy"}
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
      timestamp: net.asOf,
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

/** Consecutive failed turns after which the operator is told it is degraded (R-624). */
const DEGRADED_AFTER_FAILURES = 3;

export default function ChatPanel({
  portfolio,
  activeSection,
  onClose,
  isOpen = true,
  seedPrompt = null,
  onSeedConsumed,
  prices = NO_PRICES,
}: ChatPanelProps) {
  const [draft, setDraft] = useState<{ id: number; text: string; attachments?: ChatImageAttachment[] }>();
  const [selectedModel, setSelectedModel] = useState("");
  const [evidence, setEvidence] = useState<Record<string, TurnEvidence>>({});
  const [editing, setEditing] = useState(false);
  const editHistoryRef = useRef<Message[] | null>(null);
  const requestRef = useRef<{ prompt: string; attachments: ChatImageAttachment[]; history: Message[] } | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [status, setStatus] = useState<ChatStatus>("idle");
  const [lastError, setLastError] = useState("");
  // R-624: the per-turn copy repeats forever with no counter, so turn 1 and
  // turn 200 of a sustained provider outage look identical to the operator.
  const [consecutiveFailures, setConsecutiveFailures] = useState(0);
  const [proposal, setProposal] = useState<AssistantOrderProposal | null>(null);
  // Tool telemetry for the turn in flight. Reset per send so a finished turn's
  // trace can't leak into the next one.
  const [turnTools, setTurnTools] = useState<AssistantToolEvent[]>([]);
  const [traceExpanded, setTraceExpanded] = useState(true);
  const [turnModel, setTurnModel] = useState<string | null>(null);
  const [isPlacing, setPlacing] = useState(false);
  const [riskState, setRiskState] = useState<OrderRiskState | null>(null);
  const riskInput = useMemo(() => proposalRiskInput(proposal), [proposal]);
  const quote = useMemo(() => proposalQuote(proposal, prices), [proposal, prices]);
  const [showJump, setShowJump] = useState(false);

  const messagesRef = useRef<HTMLDivElement | null>(null);
  const atBottomRef = useRef(true);

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

  // A long turn types for many seconds. Without this, unmounting the panel
  // mid-stream left the loop running to completion, calling setMessages on a
  // component that is gone. R-312.
  const streamAbortRef = useRef<AbortController | null>(null);
  useEffect(() => {
    return () => streamAbortRef.current?.abort();
  }, []);

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

  const sendMessage = async (
    prompt: string,
    attachments: ChatImageAttachment[] = [],
    modelId = "",
  ) => {
    const cleaned = prompt.trim();
    if ((!cleaned && !attachments.length) || streamAbortRef.current || isPlacing) return;
    const controller = new AbortController();
    streamAbortRef.current = controller;
    const history = editHistoryRef.current ?? messages;
    editHistoryRef.current = null;
    setEditing(false);
    requestRef.current = { prompt: cleaned, attachments, history };
    // A new turn invalidates any prior model-controlled destructive intent.
    setProposal(null);
    setRiskState(null);

    const userMessage: Message = {
      id: `u-${Date.now()}`,
      role: "user",
      timestamp: createTimestamp(),
      content: cleaned,
      ...(attachments.length ? { attachments } : {}),
    };

    const conversation: ApiMessage[] = history
      .filter((message) => !evidence[message.id]?.failed && !evidence[message.id]?.stopped)
      .map((message) => message.role === "user"
        ? buildUserMessage(message.content, message.attachments ?? [])
        : { role: message.role, content: message.content });

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

    setMessages([...history, userMessage, assistantMessage]);
    setStatus("submitted");
    setLastError("");
    setTraceExpanded(true);
    setTurnTools([]);
    setTurnModel(null);

    // A PI command runs a script and never sees an image, so a turn carrying a
    // pasted image always goes to the assistant rather than silently dropping it.
    const piCommand = !attachments.length && cleaned.startsWith("/") ? routeToPiPrompt(cleaned) : null;

    try {
      if (piCommand) {
        const assistantContent = await requestPiReply(piCommand, controller.signal);
        if (controller.signal.aborted) return;
        setStatus("streaming");
        await streamMessage(assistantId, assistantContent, setMessages, {
          signal: controller.signal,
        });
      } else {
        // The route streams its envelope: `start` lands within milliseconds of
        // the request, and each tool call lands as the loop completes it. Both
        // are wired live so a 55s turn reads as alive rather than as a panel
        // that has stopped responding. R-262.
        const turn = await requestAssistantTurn(
          conversation,
          cleaned,
          attachments,
          modelId,
          (event) => {
            if (controller.signal.aborted) return;
            if (event.type === "start") setStatus("streaming");
            else setTurnTools((current) => [...current, event.event]);
          },
          controller.signal,
        );
        if (controller.signal.aborted) return;
        setEvidence((current) => ({ ...current, [assistantId]: { tools: turn.toolEvents, model: turn.model, failed: turn.failed } }));
        setTurnTools(turn.toolEvents);
        setTurnModel(turn.model);
        setStatus("streaming");
        await streamMessage(assistantId, turn.content, setMessages, {
          signal: controller.signal,
        });
        // F7: never auto-execute. A destructive order proposal is surfaced as
        // a confirm card the operator must explicitly accept.
        if (controller.signal.aborted) return;
        if (turn.failed) {
          setConsecutiveFailures((n) => n + 1);
          setStatus("error");
          return;
        }
        if (turn.proposal) {
          setProposal(turn.proposal);
        }
      }
      if (controller.signal.aborted) return;
      setConsecutiveFailures(0);
      setStatus("done");
    } catch (error) {
      if (controller.signal.aborted) return;
      setEvidence((current) => ({ ...current, [assistantId]: { tools: [], model: null, failed: true } }));
      const isPiCommand = Boolean(piCommand);
      const errorMessage =
        isPiCommand && error instanceof Error
          ? error.message
          : isPiCommand
            ? "Unexpected PI command error."
            : assistantErrorMessage();
      const fallbackContent = isPiCommand
        ? `PI command failed to run in this session.\n\nFallback note: ${errorMessage}`
        : errorMessage;

      setMessages((current) =>
        current.map((message) =>
          message.id === assistantId ? { ...message, content: fallbackContent } : message,
        ),
      );
      // The transcript already carries assistant failures. Keep the separate
      // error rail for PI commands and order placement so copy is said once.
      setLastError(isPiCommand ? errorMessage : "");
      setConsecutiveFailures((n) => n + 1);
      setStatus("error");
    } finally {
      if (streamAbortRef.current === controller) streamAbortRef.current = null;
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
    if (!seedPrompt || !isOpen) return;
    if (streamAbortRef.current || isPlacing) return;
    void sendMessageRef.current(seedPrompt, [], selectedModel);
    onSeedConsumedRef.current?.();
  }, [seedPrompt, isBusy, isPlacing, selectedModel, isOpen]);

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

  const setComposerDraft = (text: string, attachments: ChatImageAttachment[] = []) => {
    setDraft((current) => ({ id: (current?.id ?? 0) + 1, text, attachments }));
  };
  const stopResponse = useCallback(() => {
    if (!streamAbortRef.current) return;
    streamAbortRef.current.abort();
    streamAbortRef.current = null;
    setStatus("done");
    setProposal(null);
    if (lastAssistantId) {
      setMessages((current) => current.map((message) => message.id === lastAssistantId
        ? { ...message, content: message.content || "Response stopped." } : message));
      setEvidence((current) => ({ ...current, [lastAssistantId]: { tools: [], model: null, stopped: true } }));
    }
  }, [lastAssistantId]);
  const stopRef = useRef(stopResponse);
  stopRef.current = stopResponse;
  useEffect(() => { if (!isOpen) stopRef.current(); }, [isOpen]);

  const retry = () => {
    const request = requestRef.current;
    if (!request || isBusy || isPlacing) return;
    editHistoryRef.current = request.history;
    void sendMessage(request.prompt, request.attachments, selectedModel);
  };
  const newConversation = () => {
    if (isPlacing) return;
    stopResponse();
    onSeedConsumed?.();
    setConsecutiveFailures(0);
    setMessages([]);
    setEvidence({});
    setProposal(null);
    setRiskState(null);
    setStatus("idle");
    setLastError("");
    setEditing(false);
    editHistoryRef.current = null;
    requestRef.current = null;
    setComposerDraft("");
  };
  const sectionName = activeSection === "dashboard" ? "Portfolio overview"
    : activeSection === "portfolio" ? "Positions"
    : activeSection.replace(/-/g, " ").replace(/^./, (c) => c.toUpperCase());
  const starters = [
    { title: "Review portfolio risk", detail: "Concentration, exposure and downside", prompt: "Review my current portfolio risk. Identify concentration, exposure and downside, and cite the data and its freshness." },
    { title: "Investigate market flow", detail: "Positioning behind the price", prompt: "Help me investigate institutional flow. Ask which ticker to analyze, then compare dark-pool activity with price and explain what supports or contradicts the signal." },
    { title: "Pressure-test a trade", detail: "Structure, payoff and counterevidence", prompt: "Help me pressure-test an options trade. Ask for the ticker and thesis, then compare defined-risk structures, payoff, and evidence against the trade." },
  ];

  return (
    <div className="chat-panel" data-empty={messages.length === 0 ? "true" : undefined}>
      <header className="chat-header">
        <div className="chat-identity"><Activity size={20} aria-hidden="true" /><div>
          <h2>Radon AI</h2><span>Research & analysis</span>
        </div></div>
        <div className="chat-header-actions">
          <button type="button" className="chat-header-button" onClick={newConversation} disabled={isPlacing} aria-label="New conversation"><Plus size={16} /><span>New chat</span></button>
          {onClose ? <button type="button" className="chat-header-button" onClick={onClose} aria-label="Close chat"><X size={18} /></button> : null}
        </div>
      </header>
      <div className="chat-context"><span>Viewing <strong>{sectionName}</strong></span><span>Data retrieved when needed</span></div>
      <div className="chat-shell">
        <div className="chat-transcript-wrap">
          {messages.length ? (
            <div ref={messagesRef} className="chat-messages" data-testid="chat-messages"
              role="log" aria-label="Conversation" aria-live="polite" aria-atomic="false"
              aria-busy={isBusy} onScroll={onTranscriptScroll}>
              {messages.map((message, index) => {
                const isAssistant = message.role === "assistant";
                const isCurrent = message.id === lastAssistantId;
                const isPending = isAssistant && !message.content && isCurrent && isBusy;
                const isStreamingThis = isAssistant && isCurrent && status === "streaming";
                const meta = evidence[message.id];
                return <div key={message.id} className={`chat-message ${message.role}${isStreamingThis ? " streaming" : ""}`} data-testid={`chat-message-${message.role}`}>
                  <div className="chat-meta"><span className="chat-role" data-testid="chat-role">{isAssistant ? "Radon" : "You"}</span><span className="chat-time">{message.timestamp}</span></div>
                  <div className="chat-message-body" data-testid="chat-message-body">
                    {isPending ? <EngineTrace steps={buildTurnSteps(turnTools, "submitted")} engines={turnModel ? describeEngines(turnModel) : []} collapsed={!traceExpanded} onToggle={() => setTraceExpanded((expanded) => !expanded)} /> : <>
                      {message.attachments?.length ? <div className="ask-composer__attachments" aria-label="Attached images">{message.attachments.map((attachment) => <span key={attachment.id} className="ask-composer__thumb">
                        {/* eslint-disable-next-line @next/next/no-img-element */}
                        <img src={`data:${attachment.mediaType};base64,${attachment.data}`} alt={attachment.name || "Pasted image"} />
                      </span>)}</div> : null}
                      <MarkdownRenderer content={message.content} />
                      {isStreamingThis ? <span className="chat-cursor" aria-hidden="true" /> : null}
                    </>}
                  </div>
                  {!isStreamingThis && meta?.tools.length ? <details className="chat-evidence"><summary>Activity · {meta.tools.length} tool {meta.tools.length === 1 ? "call" : "calls"}</summary>
                    <ol>{buildTurnSteps(meta.tools, "done").map((step) => <li key={step.id}><span>{step.label}</span><span>{step.meta}</span></li>)}</ol>
                  </details> : null}
                  {isAssistant && message.content && !isBusy ? <div className="chat-actions">
                    <CopyButton content={message.content} />
                    {isCurrent && requestRef.current ? <button type="button" className="chat-action-btn" onClick={retry} disabled={isPlacing}><RotateCcw size={14} />{meta?.failed || meta?.stopped ? "Try again" : "Regenerate"}</button> : null}
                    {meta?.model ? <span className="chat-response-model">{meta.model}</span> : null}
                    {meta?.stopped && message.content !== "Response stopped." ? <span className="chat-response-model">Response stopped</span> : null}
                  </div> : null}
                  {!isAssistant && !isBusy ? <div className="chat-actions"><button type="button" className="chat-action-btn" disabled={isPlacing} onClick={() => {
                    editHistoryRef.current = messages.slice(0, index);
                    setEditing(true);
                    setComposerDraft(message.content, message.attachments);
                  }}><Pencil size={14} />Edit prompt</button></div> : null}
                </div>;
              })}
            </div>
          ) : <div className="chat-welcome">
            <span className="chat-welcome-eyebrow">Your research workspace</span>
            <h3>What do you want{" "}<br />to understand?</h3>
            <p>Connect your portfolio, market flow and trade ideas.{" "}<br className="chat-desktop-break" /> Start with a question, or shape one below.</p>
            <div className="chat-starters">{starters.map((starter) => <button type="button" key={starter.title} onClick={() => setComposerDraft(starter.prompt)}>
              <span><strong>{starter.title}</strong>{" "}<span>{starter.detail}</span></span><ArrowUpRight size={18} aria-hidden="true" />
            </button>)}</div>
          </div>}
          {messages.length ? <button type="button" className="chat-jump-btn" data-hidden={!showJump} onClick={jumpToBottom} aria-label="Scroll to latest" tabIndex={showJump ? 0 : -1}><ArrowDown size={14} />Latest</button> : null}
        </div>

          {consecutiveFailures >= DEGRADED_AFTER_FAILURES ? (
            <div className="chat-degraded" role="status">
              {`The assistant has failed ${consecutiveFailures} turns in a row. The provider or the backend is degraded; retrying will not help until it recovers.`}
            </div>
          ) : null}

          {lastError ? <div className="chat-error">{lastError}</div> : null}

          {/* F7: never auto-execute. TODO(agent-ui): when the assistant returns
              sized alternatives (split clips, hold), map them into `options` and
              pass the confirmed option id through to placeProposedOrder. */}
          {proposal ? (
            <div className="chat-approval-area" aria-label="Order review">
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
            </div>
          ) : null}

          <div className="chat-composer">
            {editing ? <div className="chat-editing">Editing previous prompt<button type="button" onClick={() => { editHistoryRef.current = null; setEditing(false); setComposerDraft(""); }}>Cancel edit</button></div> : null}
            <AskComposer
              draft={draft}
              onModelChange={setSelectedModel}
              onStop={isBusy ? stopResponse : undefined}
              busy={isBusy || isPlacing}
              focusKey={isOpen}
              onSubmit={(text, modelId, attachments) =>
                void sendMessage(text, attachments, modelId)
              }
            />
            <p className="chat-footnote">Verify sources and timestamps. Orders require your confirmation.</p>
          </div>
        </div>
    </div>
  );
}
