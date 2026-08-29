"use client";

import {
  type ClipboardEvent,
  type FormEvent,
  type KeyboardEvent,
  useEffect,
  useRef,
  useState,
} from "react";
import { Send } from "lucide-react";

import type { ChatImageAttachment, ChatImageMediaType } from "@/lib/types";

/**
 * AskComposer — conversational composer adopted from beautifului.dev
 * "Prompt Bar" (Vanilla variant; the pill variant is rejected — 4px radius).
 * Adds @-instrument source tokens, a / commands hint, and an engine picker
 * in place of a model picker. Preserves ChatPanel's Enter-to-send and IME
 * composition guards. Rendered inside .chat-composer.
 */

export const ENGINES = ["AUTO", "SPECTRAL", "EIGEN", "MARKOV", "LAPLACE"] as const;
export type Engine = (typeof ENGINES)[number];

/** Mirrors the server allowlist in app/api/assistant/route.ts. */
const ALLOWED_MEDIA_TYPES: ChatImageMediaType[] = [
  "image/png",
  "image/jpeg",
  "image/gif",
  "image/webp",
];
const MAX_ATTACHMENTS = 4;
const MAX_DECODED_BYTES = 5 * 1024 * 1024;

function isAllowedMediaType(type: string): type is ChatImageMediaType {
  return (ALLOWED_MEDIA_TYPES as string[]).includes(type);
}

/** Clipboard/drop payloads expose files through `items` first, `files` second. */
function imageFilesFrom(data: DataTransfer | null): File[] {
  if (!data) return [];
  const fromItems = Array.from(data.items ?? [])
    .filter((item) => item.kind === "file")
    .map((item) => item.getAsFile())
    .filter((file): file is File => file != null);
  const files = fromItems.length ? fromItems : Array.from(data.files ?? []);
  return files.filter((file) => isAllowedMediaType(file.type));
}

/** Resolves the RAW base64 payload — the "data:<mt>;base64," prefix is stripped. */
function readBase64(file: File): Promise<string | null> {
  return new Promise((resolve) => {
    const reader = new FileReader();
    reader.onerror = () => resolve(null);
    reader.onload = () => {
      const result = typeof reader.result === "string" ? reader.result : "";
      const comma = result.indexOf(",");
      resolve(comma === -1 ? null : result.slice(comma + 1));
    };
    reader.readAsDataURL(file);
  });
}

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
  onSubmit: (text: string, engine: Engine, attachments: ChatImageAttachment[]) => void;
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
  const [attachments, setAttachments] = useState<ChatImageAttachment[]>([]);
  const composingRef = useRef(false);
  const inputRef = useRef<HTMLTextAreaElement | null>(null);
  // Monotonic so a removal can never let a later paste reuse a live id.
  const nextIdRef = useRef(0);

  useEffect(() => {
    if (focusKey === false) return;
    inputRef.current?.focus();
  }, [focusKey]);

  const attach = async (files: File[]) => {
    for (const file of files) {
      const mediaType = file.type;
      if (!isAllowedMediaType(mediaType)) continue;
      const data = await readBase64(file);
      if (!data) continue;
      // Contract: decoded bytes, not base64 length.
      if ((data.length * 3) / 4 > MAX_DECODED_BYTES) continue;
      const id = `${nextIdRef.current++}-${file.name || mediaType}`;
      setAttachments((prev) =>
        prev.length >= MAX_ATTACHMENTS
          ? prev
          : [...prev, { id, mediaType, data, name: file.name || undefined }],
      );
    }
  };

  const onPaste = (event: ClipboardEvent<HTMLTextAreaElement>) => {
    const files = imageFilesFrom(event.clipboardData);
    if (!files.length) return;
    // Only swallow the paste once we know it is an image; text keeps its default.
    event.preventDefault();
    void attach(files);
  };

  const submit = () => {
    const cleaned = text.trim();
    if ((!cleaned && !attachments.length) || busy) return;
    onSubmit(cleaned, engine, attachments);
    setText("");
    setAttachments([]);
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
      {attachments.length ? (
        <div className="ask-composer__attachments" aria-label="Attached images">
          {attachments.map((a) => (
            <span key={a.id} className="ask-composer__thumb">
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src={`data:${a.mediaType};base64,${a.data}`} alt={a.name || "Pasted image"} />
              <button
                type="button"
                className="ask-composer__thumb-remove"
                aria-label="Remove image"
                title={a.name ? `Remove ${a.name}` : "Remove image"}
                onClick={() =>
                  setAttachments((prev) => prev.filter((candidate) => candidate.id !== a.id))
                }
              >
                ×
              </button>
            </span>
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
        onPaste={onPaste}
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
          disabled={(!text.trim() && !attachments.length) || busy}
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
