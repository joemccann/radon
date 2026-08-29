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

import type { LlmModelOption } from "@/lib/llm/catalog";
import type { ChatImageAttachment, ChatImageMediaType } from "@/lib/types";

/**
 * AskComposer — conversational composer adopted from beautifului.dev
 * "Prompt Bar" (Vanilla variant; the pill variant is rejected — 4px radius).
 * Adds @-instrument source tokens, a / commands hint, and a model picker.
 * Preserves ChatPanel's Enter-to-send and IME composition guards. Rendered
 * inside .chat-composer.
 *
 * The picker is DERIVED, never compiled in: GET /api/models lists one entry per
 * provider whose API key is present in this deployment, so a single-provider
 * host honestly shows a single-entry select and the others appear the moment
 * their keys land. Until the catalog answers — and if it never does — the model
 * id is the empty string, which ChatPanel reads as "let the server decide".
 */

/** Placeholder occupant of the select so the rail never reflows on load. */
const SERVER_DEFAULT_LABEL = "DEFAULT";

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
  /**
   * `modelId` is a catalog id, or "" when no catalog answered — an empty id
   * means the request carries no model and the server picks as it always has.
   */
  onSubmit: (text: string, modelId: string, attachments: ChatImageAttachment[]) => void;
  /**
   * Fires whenever the effective selection changes: once when the catalog
   * resolves, then on every pick. The panel needs it because it sends turns of
   * its own (the starter-prompt pills) that must run on the model the operator
   * can see selected, not on the server default.
   */
  onModelChange?: (modelId: string) => void;
};

export default function AskComposer({
  placeholder = "Ask about flow, risk, structure. @ to scope an instrument, / for commands.",
  busy = false,
  sources = [],
  onRemoveSource,
  focusKey,
  onSubmit,
  onModelChange,
}: AskComposerProps) {
  const [text, setText] = useState("");
  const [models, setModels] = useState<LlmModelOption[]>([]);
  const [modelId, setModelId] = useState("");
  const [attachments, setAttachments] = useState<ChatImageAttachment[]>([]);
  const composingRef = useRef(false);
  const inputRef = useRef<HTMLTextAreaElement | null>(null);
  // Monotonic so a removal can never let a later paste reuse a live id.
  const nextIdRef = useRef(0);
  // Read through a ref so the one-shot catalog effect keeps its [] deps and
  // still calls the CURRENT callback, never a first-render closure.
  const onModelChangeRef = useRef(onModelChange);
  onModelChangeRef.current = onModelChange;


  useEffect(() => {
    if (focusKey === false) return;
    inputRef.current?.focus();
  }, [focusKey]);

  // One read of the deployment's live catalog. A failure is not an error state:
  // the composer keeps working on the server's own default rather than blocking
  // the operator behind a model list.
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const response = await fetch("/api/models", { cache: "no-store" });
        if (!response.ok) return;
        const payload = (await response.json()) as {
          models?: LlmModelOption[];
          defaultId?: string;
        };
        const catalog = Array.isArray(payload?.models) ? payload.models : [];
        if (cancelled || !catalog.length) return;
        const fallback = catalog[0].id;
        const preferred =
          typeof payload.defaultId === "string" &&
          catalog.some((option) => option.id === payload.defaultId)
            ? payload.defaultId
            : fallback;
        setModels(catalog);
        setModelId(preferred);
        onModelChangeRef.current?.(preferred);
      } catch {
        // Offline or route absent: stay on the server default.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

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
    onSubmit(cleaned, modelId, attachments);
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
        <label className="ask-composer__model">
          <span className="ask-composer__model-label">MODEL</span>
          <select
            value={modelId}
            aria-label="Model"
            onChange={(event) => {
              setModelId(event.target.value);
              onModelChange?.(event.target.value);
            }}
          >
            {models.length ? (
              models.map((option) => (
                <option key={option.id} value={option.id}>
                  {option.label}
                </option>
              ))
            ) : (
              <option value="">{SERVER_DEFAULT_LABEL}</option>
            )}
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
