"use client";

import { useEffect, useId, useRef, useState } from "react";
import {
  formatAgentPrompt,
  writeClipboard,
  type AgentPrompt,
} from "@/lib/agent-prompts";

interface Props {
  prompt: AgentPrompt;
  showView?: boolean;
}

const focusRing =
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/60 focus-visible:ring-offset-2 focus-visible:ring-offset-canvas";

const buttonClass = `inline-block rounded-[4px] border border-grid px-[13px] py-[7px] font-mono text-[11px] tracking-[0.04em] text-primary transition-colors hover:border-signal-deep hover:text-signal-deep ${focusRing}`;

export function CopyAgentPrompt({ prompt, showView = true }: Props) {
  const markdown = formatAgentPrompt(prompt);
  const titleId = useId();
  const [status, setStatus] = useState<"idle" | "copied" | "failed">("idle");
  const [open, setOpen] = useState(false);
  const dialogRef = useRef<HTMLDialogElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    const dialog = dialogRef.current;
    dialog?.showModal();
    return () => {
      dialog?.close();
      triggerRef.current?.focus();
    };
  }, [open]);

  async function copy() {
    setStatus(await writeClipboard(markdown));
  }

  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
      <button type="button" className={buttonClass} onClick={() => void copy()}>
        Copy agent prompt
      </button>
      {showView ? (
        <button
          type="button"
          className={`font-mono text-[11px] tracking-[0.04em] text-secondary underline decoration-grid underline-offset-4 transition-colors hover:text-signal-deep ${focusRing}`}
          ref={triggerRef}
          onClick={() => setOpen(true)}
        >
          View prompt
        </button>
      ) : null}
      <span
        role="status"
        aria-live="polite"
        className="font-mono text-[11px] uppercase tracking-[0.08em] text-signal-deep"
      >
        {status === "copied" ? "Copied" : status === "failed" ? "Copy failed" : ""}
      </span>
      {open ? (
        <dialog
          ref={dialogRef}
          aria-labelledby={titleId}
          onClose={() => setOpen(false)}
          onKeyDown={(event) => {
            if (event.key === "Tab") {
              event.preventDefault();
              event.currentTarget.querySelector<HTMLButtonElement>("button")?.focus();
            }
          }}
          onClick={(event) => {
            if (event.target !== event.currentTarget) return;
            const bounds = event.currentTarget.getBoundingClientRect();
            if (event.clientX < bounds.left || event.clientX > bounds.right ||
                event.clientY < bounds.top || event.clientY > bounds.bottom) setOpen(false);
          }}
          className="m-auto max-h-[80vh] w-[calc(100%_-_3rem)] max-w-[760px] overflow-auto rounded-[4px] border border-grid bg-figure-bg p-6 backdrop:bg-canvas/80"
        >
            <div className="mb-4 flex items-start justify-between gap-4">
              <h2
                id={titleId}
                className="font-serif text-[1.24rem] font-medium text-primary"
              >
                Agent prompt
              </h2>
              <button
                type="button"
                className={buttonClass}
                onClick={() => setOpen(false)}
              >
                Close
              </button>
            </div>
            <pre className="whitespace-pre-wrap font-mono text-[12px] leading-[1.55] text-secondary">
              {markdown}
            </pre>
        </dialog>
      ) : null}
    </div>
  );
}
