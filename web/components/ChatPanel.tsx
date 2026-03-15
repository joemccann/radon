"use client";

import { type FormEvent, useEffect, useRef, useState } from "react";
import { Bot, Send } from "lucide-react";
import type { ApiMessage, Message, WorkspaceSection } from "@/lib/types";
import { quickPromptsBySection } from "@/lib/data";
import { createTimestamp } from "@/lib/utils";
import { fallbackReply, requestAssistantReply, requestPiReply, routeToPiPrompt, streamMessage } from "@/lib/chat";
import MarkdownRenderer from "./MarkdownRenderer";

type ChatPanelProps = {
  activeSection: WorkspaceSection;
};

export default function ChatPanel({ activeSection }: ChatPanelProps) {
  const [query, setQuery] = useState("");
  const [messages, setMessages] = useState<Message[]>([]);
  const [isBusy, setBusy] = useState(false);
  const [lastError, setLastError] = useState("");
  const messagesRef = useRef<HTMLDivElement | null>(null);
  const sectionPrompts = quickPromptsBySection[activeSection];

  useEffect(() => {
    if (messagesRef.current) {
      messagesRef.current.scrollTop = messagesRef.current.scrollHeight;
    }
  }, [messages]);

  const sendMessage = async (eventOrPrompt: FormEvent<HTMLFormElement> | string) => {
    if (typeof eventOrPrompt !== "string") {
      eventOrPrompt.preventDefault();
    }

    const nextPrompt = typeof eventOrPrompt === "string" ? eventOrPrompt : query;
    const cleaned = nextPrompt.trim();
    if (!cleaned) {
      return;
    }

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

    setMessages((current) => [...current, userMessage]);
    setQuery("");
    setBusy(true);
    setLastError("");

    try {
      const piCommand = routeToPiPrompt(cleaned);
      const isCommand = Boolean(piCommand);

      if (isCommand) {
        const assistantId = `a-${Date.now()}-pi`;
        const assistantMessage: Message = {
          id: assistantId,
          role: "assistant",
          timestamp: createTimestamp(),
          content: "",
        };
        setMessages((current) => [...current, assistantMessage]);
        const assistantContent = await requestPiReply(piCommand || cleaned);
        await streamMessage(assistantId, assistantContent, setMessages);
      } else {
        const assistantContent = await requestAssistantReply(conversation, cleaned);
        const assistantMessage: Message = {
          id: `a-${Date.now()}`,
          role: "assistant",
          timestamp: createTimestamp(),
          content: assistantContent,
        };
        setMessages((current) => [...current, assistantMessage]);
      }
    } catch (error) {
      const isPiCommand = Boolean(routeToPiPrompt(cleaned));
      const fallback = isPiCommand ? "PI command failed to run in this session." : fallbackReply(cleaned);
      const errorMessage =
        error instanceof Error
          ? error.message
          : isPiCommand
            ? "Unexpected PI command error."
            : "Unexpected assistant error.";

      setMessages((current) => [
        ...current,
        {
          id: `a-${Date.now()}`,
          role: "assistant",
          timestamp: createTimestamp(),
          content: `${fallback}\n\nFallback note: ${errorMessage}`,
        },
      ]);
      setLastError(errorMessage);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className={`section chat-panel ${activeSection === "dashboard" ? "dashboard-chat-panel" : ""}`}>
      <div className="section-header">
        <div className="section-title">
          <Bot size={14} />
          Radon Chat
        </div>
        <span className="pill defined">LIVE CONVERSATION</span>
      </div>
      <div className="section-body">
        <div className="chat-shell">
          <form suppressHydrationWarning className="chat-input-row" onSubmit={sendMessage}>
            <textarea
              suppressHydrationWarning
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Ask Pi for flow analysis, risk checks, action items..."
              className="chat-textarea"
              aria-label="Message Pi assistant"
              rows={6}
              onKeyDown={(event) => {
                if (event.key === "Enter" && !event.shiftKey) {
                  event.preventDefault();
                  sendMessage(query);
                }
              }}
              maxLength={400}
            />
            <button
              suppressHydrationWarning
              className="chat-send"
              type="submit"
              disabled={!query.trim()}
              title="Send (Enter)"
              aria-label="Send message"
            >
              <Send size={14} />
            </button>
          </form>

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

          {lastError ? <div className="chat-error">{lastError}</div> : null}

          {messages.length ? (
            <div ref={messagesRef} className="chat-messages" aria-live="polite">
              {messages.map((message) => (
                <div
                  key={message.id}
                  className={`chat-message ${message.role}${
                    message.role === "assistant" && !message.content ? " streaming" : ""
                  }`}
                >
                  <div className="chat-meta">
                    <span className="chat-role">{message.role === "assistant" ? "Pi" : "You"}</span>
                    <span className="chat-time">{message.timestamp}</span>
                  </div>
                  <div className="chat-message-body">
                    <MarkdownRenderer content={message.content} />
                  </div>
                </div>
              ))}
              {isBusy ? (
                <div className="chat-message assistant streaming">
                  <div className="chat-meta">
                    <span className="chat-role">Pi</span>
                    <span className="chat-time">processing...</span>
                  </div>
                  <div className="chat-message-body">
                    <div className="chat-content">
                      <div className="chat-line">Analyzing flow, structure, and risk context...</div>
                    </div>
                  </div>
                </div>
              ) : null}
            </div>
          ) : null}
        </div>
      </div>
    </div>
  );
}
