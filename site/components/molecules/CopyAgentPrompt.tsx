"use client";

import { useId, useState } from "react";
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
        <div
          className="fixed inset-0 z-[70] flex items-center justify-center bg-canvas/80 p-6"
          role="presentation"
          onClick={() => setOpen(false)}
        >
          <div
            role="dialog"
            aria-modal="true"
            aria-labelledby={titleId}
            className="max-h-[80vh] w-full max-w-[760px] overflow-auto rounded-[4px] border border-grid bg-figure-bg p-6"
            onClick={(event) => event.stopPropagation()}
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
          </div>
        </div>
      ) : null}
    </div>
  );
}
