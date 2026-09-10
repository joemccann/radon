"use client";

import {
  type ClipboardEvent,
  type FormEvent,
  type KeyboardEvent,
  useEffect,
  useId,
  useRef,
  useState,
} from "react";

import type { LlmModelOption } from "@/lib/llm/catalog";
import type { ChatImageAttachment, ChatImageMediaType } from "@/lib/types";

/** Deployment-derived model selection and a draft-preserving multimodal composer. */

/** Placeholder occupant of the select so the rail never reflows on load. */
const SERVER_DEFAULT_LABEL = "Server default";

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

/** Clipboard payloads expose files through `items` first, `files` second. */
function imageFilesFrom(data: DataTransfer | null): File[] {
  if (!data) return [];
  const fromItems = Array.from(data.items ?? [])
    .filter((item) => item.kind === "file")
    .map((item) => item.getAsFile())
    .filter((file): file is File => file != null);
  const files = fromItems.length ? fromItems : Array.from(data.files ?? []);
  return files;
}

/** Resolves the RAW base64 payload — the "data:<mt>;base64," prefix is stripped. */
function readBase64(file: File): Promise<string | null> {
  return new Promise((resolve) => {
    const reader = new FileReader();
    reader.onerror = () => resolve(null);
    reader.onabort = () => resolve(null);
    reader.onload = () => {
      const result = typeof reader.result === "string" ? reader.result : "";
      const comma = result.indexOf(",");
      resolve(comma === -1 ? null : result.slice(comma + 1));
    };
    try {
      reader.readAsDataURL(file);
    } catch {
      resolve(null);
    }
  });
}

type AskComposerProps = {
  placeholder?: string;
  busy?: boolean;
  onStop?: () => void;
  /** A changed id replaces the draft, including restored attachments, and focuses it. */
  draft?: { id: number; text: string; attachments?: ChatImageAttachment[] };
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
   * its own that must run on the model the operator can see selected.
   */
  onModelChange?: (modelId: string) => void;
};

export default function AskComposer({
  placeholder = "Ask about your portfolio, risk, or a trade…",
  busy = false,
  onStop,
  draft,
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
  const [attachmentErrors, setAttachmentErrors] = useState<string[]>([]);
  const [pendingReads, setPendingReads] = useState(0);
  const helpId = useId();
  const errorId = useId();
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const attachmentsRef = useRef<ChatImageAttachment[]>([]);
  const pendingReadsRef = useRef(0);
  const attachmentEpochRef = useRef(0);
  const draftIdRef = useRef<number | undefined>(undefined);
  const composingRef = useRef(false);
  const inputRef = useRef<HTMLTextAreaElement | null>(null);
  // Monotonic so a removal can never let a later paste reuse a live id.
  const nextIdRef = useRef(0);
  // Read through a ref so the one-shot catalog effect keeps its [] deps and
  // still calls the CURRENT callback, never a first-render closure.
  const onModelChangeRef = useRef(onModelChange);
  onModelChangeRef.current = onModelChange;

  useEffect(() => {
    if (!draft || draft.id === draftIdRef.current) return;
    draftIdRef.current = draft.id;
    attachmentEpochRef.current += 1;
    pendingReadsRef.current = 0;
    setPendingReads(0);
    setAttachmentErrors([]);
    attachmentsRef.current = draft.attachments ?? [];
    setAttachments(attachmentsRef.current);
    setText(draft.text);
    inputRef.current?.focus();
  }, [draft]);

  useEffect(() => () => { attachmentEpochRef.current += 1; }, []);

  useEffect(() => {
    const input = inputRef.current;
    if (!input) return;
    input.style.height = "auto";
    input.style.height = `${Math.min(Math.max(input.scrollHeight, 64), 180)}px`;
  }, [text]);

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
    const epoch = attachmentEpochRef.current;
    const errors: string[] = [];
    const accepted: { file: File; mediaType: ChatImageMediaType }[] = [];
    for (const file of files) {
      if (!isAllowedMediaType(file.type)) {
        errors.push(`${file.name || "This file"}: use a PNG, JPEG, GIF, or WebP image.`);
      } else if (file.size > MAX_DECODED_BYTES) {
        errors.push(`${file.name || "This image"}: images must be 5 MB or smaller.`);
      } else if (attachmentsRef.current.length + pendingReadsRef.current >= MAX_ATTACHMENTS) {
        errors.push(`${file.name || "This image"}: attach up to 4 images per message.`);
      } else {
        pendingReadsRef.current += 1;
        accepted.push({ file, mediaType: file.type });
      }
    }
    setAttachmentErrors(errors);
    setPendingReads(pendingReadsRef.current);
    const results = await Promise.all(accepted.map(async ({ file, mediaType }) => ({
      file, mediaType, data: await readBase64(file),
    })));
    // A replaced draft or unmount invalidates every outstanding read.
    if (epoch !== attachmentEpochRef.current) return;
    const loaded: ChatImageAttachment[] = [];
    const readErrors: string[] = [];
    for (const { file, mediaType, data } of results) {
      if (!data || Math.floor((data.length * 3) / 4) > MAX_DECODED_BYTES) {
        readErrors.push(`${file.name || "This image"}: could not attach. Try an image smaller than 5 MB.`);
      } else {
        const id = `attachment-${epoch}-${nextIdRef.current++}-${file.name || mediaType}`;
        loaded.push({ id, mediaType, data, name: file.name || undefined });
      }
    }
    attachmentsRef.current = [...attachmentsRef.current, ...loaded];
    setAttachments(attachmentsRef.current);
    if (readErrors.length) setAttachmentErrors((previous) => [...previous, ...readErrors]);
    pendingReadsRef.current -= accepted.length;
    setPendingReads(pendingReadsRef.current);
  };

  const onPaste = (event: ClipboardEvent<HTMLTextAreaElement>) => {
    const files = imageFilesFrom(event.clipboardData);
    if (!files.length) return;
    // File pastes are handled here; plain text keeps its browser behavior.
    event.preventDefault();
    void attach(files);
  };

  const submit = () => {
    const cleaned = text.trim();
    if ((!cleaned && !attachments.length) || busy || pendingReadsRef.current > 0) return;
    onSubmit(cleaned, modelId, attachments);
    attachmentEpochRef.current += 1;
    setText("");
    attachmentsRef.current = [];
    setAttachments([]);
    setAttachmentErrors([]);
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
                onClick={() => {
                  attachmentsRef.current = attachmentsRef.current.filter((candidate) => candidate.id !== a.id);
                  setAttachments(attachmentsRef.current);
                }}
              >
                ×
              </button>
            </span>
          ))}
        </div>
      ) : null}
      <div className="ask-composer__field">
        <textarea
          ref={inputRef}
          className="ask-composer__input"
          value={text}
          rows={2}
          maxLength={1000}
          placeholder={placeholder}
          aria-label="Ask Radon"
          aria-describedby={`${helpId}${attachmentErrors.length ? ` ${errorId}` : ""}`}
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
      </div>
      {attachmentErrors.length ? (
        <p className="ask-composer__error" id={errorId} role="alert">
          {attachmentErrors.join(" ")}
        </p>
      ) : null}
      <div className="ask-composer__rail">
        <input
          ref={fileInputRef}
          type="file"
          className="ask-composer__file-input"
          accept={ALLOWED_MEDIA_TYPES.join(",")}
          multiple
          hidden
          aria-label="Choose images"
          onChange={(event) => {
            const files = Array.from(event.target.files ?? []);
            event.target.value = "";
            if (files.length) void attach(files);
          }}
        />
        <button type="button" className="ask-composer__attach" aria-label="Attach images" onClick={() => fileInputRef.current?.click()} title="Attach images (up to 4, 5 MB each)">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true"><path d="m8 12 6.5-6.5a3.5 3.5 0 0 1 5 5L10 20a5 5 0 0 1-7-7L13 3" /><path d="m6 15 9-9" /></svg>
          <span>Attach images</span>
        </button>
        <span className="ask-composer__spacer" />
        <label className="ask-composer__model">
          <span className="ask-composer__model-label">Model</span>
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
        {busy && onStop ? (
          <button type="button" className="ask-composer__stop" onClick={onStop} aria-label="Stop response">
            <svg width="14" height="14" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true"><rect x="3" y="3" width="10" height="10" rx="1" /></svg>
            <span>Stop</span>
          </button>
        ) : (
          <button
            type="submit"
            className="ask-composer__enter"
            disabled={(!text.trim() && !attachments.length) || busy || pendingReads > 0}
            title="Send (Enter)"
            aria-label="Send"
          >
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M12 19V5m-6 6 6-6 6 6" /></svg>
          </button>
        )}
      </div>
      <p className="ask-composer__help" id={helpId}>
        {pendingReads > 0 ? <span role="status">Preparing images… </span> : null}
        <span>Enter to send · Shift+Enter for a new line</span>
      </p>
    </form>
  );
}
