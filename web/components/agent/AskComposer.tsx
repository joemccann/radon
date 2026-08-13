"use client";

import {
  type FormEvent,
  type KeyboardEvent,
  useEffect,
  useRef,
  useState,
} from "react";
import { Send } from "lucide-react";

/**
 * AskComposer — conversational composer adopted from beautifului.dev
 * "Prompt Bar" (Vanilla variant; the pill variant is rejected — 4px radius).
 * Adds @-instrument source tokens, a / commands hint, and an engine picker
 * in place of a model picker. Preserves ChatPanel's Enter-to-send and IME
 * composition guards. Rendered inside .chat-composer.
 */

export const ENGINES = ["AUTO", "SPECTRAL", "EIGEN", "MARKOV", "LAPLACE"] as const;
export type Engine = (typeof ENGINES)[number];

type AskComposerProps = {
  placeholder?: string;
  busy?: boolean;
  /** Scoped instruments shown as source tokens, e.g. ["MU", "NDX"]. */
  sources?: string[];
  onRemoveSource?: (symbol: string) => void;
  /**
   * Focuses the textarea on mount and whenever this value changes. Pass the
   * panel-open flag / open count so the ⌘J autofocus behavior is preserved
   * (replaces ChatPanel's composerRef focus effect).
   */
  focusKey?: string | number | boolean;
  onSubmit: (text: string, engine: Engine) => void;
};

export default function AskComposer({
  placeholder = "Ask about flow, risk, structure. @ to scope an instrument, / for commands.",
  busy = false,
  sources = [],
  onRemoveSource,
  focusKey,
  onSubmit,
}: AskComposerProps) {
  const [text, setText] = useState("");
  const [engine, setEngine] = useState<Engine>("AUTO");
  const composingRef = useRef(false);
  const inputRef = useRef<HTMLTextAreaElement | null>(null);

  useEffect(() => {
    if (focusKey === false) return;
    inputRef.current?.focus();
  }, [focusKey]);

  const submit = () => {
    const cleaned = text.trim();
    if (!cleaned || busy) return;
    onSubmit(cleaned, engine);
    setText("");
    inputRef.current?.focus();
  };

  const onFormSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    submit();
  };

  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (
      event.key === "Enter" &&
      !event.shiftKey &&
      !composingRef.current &&
      !event.nativeEvent.isComposing
    ) {
      event.preventDefault();
      submit();
    }
  };

  return (
    <form className="ask-composer" onSubmit={onFormSubmit}>
      {sources.length ? (
        <div className="ask-composer__sources" aria-label="Scoped instruments">
          {sources.map((s) => (
            <button
              key={s}
              type="button"
              className="ask-composer__source"
              onClick={() => onRemoveSource?.(s)}
              title={`Remove @${s}`}
            >
              @{s}
            </button>
          ))}
        </div>
      ) : null}
      <textarea
        ref={inputRef}
        className="ask-composer__input"
        value={text}
        rows={1}
        maxLength={1000}
        placeholder={placeholder}
        aria-label="Ask Radon"
        onChange={(e) => setText(e.target.value)}
        onKeyDown={onKeyDown}
        onCompositionStart={() => {
          composingRef.current = true;
        }}
        onCompositionEnd={() => {
          composingRef.current = false;
        }}
      />
      <div className="ask-composer__rail">
        <span className="agent-chip">@ SOURCES</span>
        <span className="agent-chip">/ COMMANDS</span>
        <span className="ask-composer__spacer" />
        <label className="ask-composer__engine">
          <span className="ask-composer__engine-label">ENGINE</span>
          <select
            value={engine}
            aria-label="Engine"
            onChange={(e) => setEngine(e.target.value as Engine)}
          >
            {ENGINES.map((e) => (
              <option key={e} value={e}>
                {e}
              </option>
            ))}
          </select>
        </label>
        <button
          type="submit"
          className="ask-composer__send"
          disabled={!text.trim() || busy}
          title="Send (Enter)"
          aria-label="Send"
        >
          <Send size={13} />
          ASK
        </button>
      </div>
    </form>
  );
}
