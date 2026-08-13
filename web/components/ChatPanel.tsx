"use client";

import { type FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Send, ArrowDown, Copy, Check } from "lucide-react";
import type { ApiMessage, AssistantOrderProposal, Message, PortfolioData, WorkspaceSection } from "@/lib/types";
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
import { OrderRiskGate } from "@/lib/order/risk/OrderRiskGate";
import type { OrderRiskInput, OrderRiskState } from "@/lib/order/risk/useOrderRisk";

type ChatPanelProps = {
  activeSection: WorkspaceSection;
  portfolio?: PortfolioData | null;
};

/**
 * Request lifecycle as a named union, not scattered booleans. Each state maps
 * to exactly one visual treatment: `submitted` → typing dots, `streaming` →
 * tokens + cursor, `done`/`error` → settled bubble. Kills the "No output."
 * flash that the old `isBusy` boolean produced.
 */
type ChatStatus = "idle" | "submitted" | "streaming" | "done" | "error";

const STICK_THRESHOLD_PX = 80;

function TypingIndicator() {
  return (
    <div className="chat-typing" aria-hidden="true">
      <span className="chat-typing-dot" />
      <span className="chat-typing-dot" />
      <span className="chat-typing-dot" />
    </div>
  );
}

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

export default function ChatPanel({ activeSection, portfolio }: ChatPanelProps) {
  const [query, setQuery] = useState("");
  const [messages, setMessages] = useState<Message[]>([]);
  const [status, setStatus] = useState<ChatStatus>("idle");
  const [lastError, setLastError] = useState("");
  const [proposal, setProposal] = useState<AssistantOrderProposal | null>(null);
  const [isPlacing, setPlacing] = useState(false);
  const [riskState, setRiskState] = useState<OrderRiskState | null>(null);
  const riskInput = useMemo(() => proposalRiskInput(proposal), [proposal]);
  const [showJump, setShowJump] = useState(false);

  const messagesRef = useRef<HTMLDivElement | null>(null);
  const atBottomRef = useRef(true);
  const composingRef = useRef(false);
  const composerRef = useRef<HTMLTextAreaElement | null>(null);

  // ChatPanel mounts inside the ⌘J overlay; the operator expects to type
  // immediately, so the composer takes focus on open.
  useEffect(() => {
    composerRef.current?.focus();
  }, []);
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

  const sendMessage = async (eventOrPrompt: FormEvent<HTMLFormElement> | string) => {
    if (typeof eventOrPrompt !== "string") {
      eventOrPrompt.preventDefault();
    }

    const nextPrompt = typeof eventOrPrompt === "string" ? eventOrPrompt : query;
    const cleaned = nextPrompt.trim();
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
    setQuery("");
    setStatus("submitted");
    setLastError("");

    try {
      const piCommand = routeToPiPrompt(cleaned);
      if (piCommand) {
        const assistantContent = await requestPiReply(piCommand);
        setStatus("streaming");
        await streamMessage(assistantId, assistantContent, setMessages);
      } else {
        const turn = await requestAssistantTurn(conversation, cleaned);
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
          content: result.ok ? `Order placed: ${proposal.summary}` : `Order failed: ${result.message}`,
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

  const onComposerKeyDown = (event: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (
      event.key === "Enter" &&
      !event.shiftKey &&
      !composingRef.current &&
      !event.nativeEvent.isComposing
    ) {
      event.preventDefault();
      void sendMessage(query);
    }
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
                          <TypingIndicator />
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

          {proposal ? (
            <div className="chat-order-confirm" role="group" aria-label="Order confirmation">
              <div className="chat-order-confirm-header">CONFIRM ORDER</div>
              <div className="chat-order-confirm-summary">{proposal.summary}</div>
              <OrderRiskGate
                input={riskInput}
                portfolio={portfolio}
                surface="assistant-chat"
                onState={setRiskState}
              />
              <div className="chat-order-confirm-note">This order is not sent until you confirm.</div>
              <div className="chat-order-confirm-actions">
                <button
                  type="button"
                  className="btn-secondary"
                  onClick={cancelProposal}
                  disabled={isPlacing}
                >
                  Cancel
                </button>
                <button
                  type="button"
                  className="btn-primary"
                  onClick={confirmProposal}
                  disabled={isPlacing}
                >
                  {isPlacing ? "Placing..." : "Confirm"}
                </button>
              </div>
            </div>
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
            <form suppressHydrationWarning className="chat-input-row" onSubmit={sendMessage}>
              <textarea
                suppressHydrationWarning
                ref={composerRef}
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                onKeyDown={onComposerKeyDown}
                onCompositionStart={() => {
                  composingRef.current = true;
                }}
                onCompositionEnd={() => {
                  composingRef.current = false;
                }}
                placeholder="Ask Grok for flow analysis, risk checks, action items..."
                className="chat-textarea"
                aria-label="Message Grok assistant"
                rows={1}
                maxLength={1000}
              />
              <button
                suppressHydrationWarning
                className="chat-send"
                type="submit"
                disabled={!query.trim() || isBusy}
                title="Send (Enter)"
                aria-label="Send message"
              >
                <Send size={14} />
              </button>
            </form>
          </div>
        </div>
    </div>
  );
}
