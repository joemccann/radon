"use client";

import { Share2, X } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { useDialogChrome } from "@/lib/useDialogChrome";

type ShareResponse = {
  preview_path?: string;
  error?: string;
  stale?: boolean;
  data_date?: string;
  expected_date?: string;
};

function buildStaleNotice(data: ShareResponse): string | null {
  if (!data.stale) return null;
  const dataDate = data.data_date ?? "an earlier session";
  const expected = data.expected_date ?? "the latest closed session";
  return `STALE DATA — this report reflects ${dataDate}; the latest closed session is ${expected}. Wait for the data sync to land before posting.`;
}

type ShareReportModalProps = {
  modalTitle: string;
  shareEndpoint: string;
  contentEndpoint?: string;
  buttonLabel?: string;
  buttonTitle?: string;
  enabled?: boolean;
  shareContentTitle?: string;
  iconSize?: number;
};

export default function ShareReportModal({
  modalTitle,
  shareEndpoint,
  contentEndpoint,
  buttonLabel = "Share to X",
  buttonTitle,
  enabled = true,
  shareContentTitle = "Share Preview",
  iconSize = 11,
}: ShareReportModalProps) {
  const [sharing, setSharing] = useState(false);
  const [shareError, setShareError] = useState<string | null>(null);
  const [staleNotice, setStaleNotice] = useState<string | null>(null);
  const [shareUrl, setShareUrl] = useState<string | null>(null);
  const [modalOpen, setModalOpen] = useState(false);

  const resolvedContentEndpoint = contentEndpoint ?? `${shareEndpoint}/content`;

  const closeModal = useCallback(() => {
    setModalOpen(false);
    const currentUrl = shareUrl;
    if (currentUrl) {
      setTimeout(() => {
        URL.revokeObjectURL(currentUrl);
        setShareUrl((prev) => (prev === currentUrl ? null : prev));
      }, 300);
    }
  }, [shareUrl]);

  const dialogOpen = modalOpen && shareUrl != null;
  const { panelRef } = useDialogChrome<HTMLDivElement>({
    open: dialogOpen,
    onClose: closeModal,
  });

  useEffect(() => {
    return () => {
      if (shareUrl) URL.revokeObjectURL(shareUrl);
    };
  }, [shareUrl]);

  async function handleShare() {
    if (!enabled) return;

    setSharing(true);
    setShareError(null);

    try {
      const res = await fetch(shareEndpoint, { method: "POST" });
      const data = await res.json() as ShareResponse;

      if (!res.ok) {
        setShareError(data?.error ?? "Share generation failed");
        return;
      }

      const previewPath = data?.preview_path;
      if (!previewPath) {
        setShareError("Share generation did not return a preview path.");
        return;
      }

      setStaleNotice(buildStaleNotice(data));

      const htmlRes = await fetch(
        `${resolvedContentEndpoint}?path=${encodeURIComponent(previewPath)}`
      );
      if (!htmlRes.ok) {
        setShareError("Could not load preview.");
        return;
      }

      const html = await htmlRes.text();
      const blob = new Blob([html], { type: "text/html" });

      setShareUrl((previous) => {
        if (previous) URL.revokeObjectURL(previous);
        return URL.createObjectURL(blob);
      });
      setModalOpen(true);
    } catch (err) {
      setShareError(err instanceof Error ? err.message : "Unknown error");
    } finally {
      setSharing(false);
    }
  }

  if (!enabled) return null;

  return (
    <>
      <button
        onClick={handleShare}
        disabled={sharing}
        title={buttonTitle ?? buttonLabel}
        style={{
          display: "inline-flex",
          alignItems: "center",
          gap: "5px",
          padding: "4px 10px",
          background: sharing ? "var(--bg-hover)" : "transparent",
          border: "1px solid var(--border-dim)",
          borderRadius: "3px",
          fontFamily: "var(--font-mono, monospace)",
          fontSize: "9px",
          fontWeight: 700,
          letterSpacing: "0.08em",
          textTransform: "uppercase",
          color: sharing ? "var(--text-muted)" : "var(--text-primary)",
          cursor: sharing ? "not-allowed" : "pointer",
          transition: "all 150ms",
          flexShrink: 0,
        }}
        onMouseEnter={(e) => {
          if (!sharing) {
            const target = e.currentTarget;
            target.style.borderColor = "var(--signal-core)";
            target.style.color = "var(--signal-core)";
          }
        }}
        onMouseLeave={(e) => {
          const target = e.currentTarget;
          target.style.borderColor = "var(--border-dim)";
          target.style.color = sharing ? "var(--text-muted)" : "var(--text-primary)";
        }}
      >
        <Share2 size={iconSize} />
        {sharing ? "Generating…" : buttonLabel}
      </button>
      {shareError && (
        <div
          style={{
            margin: "8px 12px",
            padding: "7px 10px",
            border: "1px solid var(--negative)",
            borderRadius: "3px",
            background: "color-mix(in srgb, var(--negative) 6%, transparent)",
            fontFamily: "var(--font-mono, monospace)",
            fontSize: "10px",
            color: "var(--negative)",
          }}
        >
          {shareError}
        </div>
      )}
      {dialogOpen && (
        <div
          className="cta-share-backdrop"
          onClick={closeModal}
          role="dialog"
          aria-modal="true"
          aria-label={shareContentTitle}
        >
          <div
            className="cta-share-modal"
            ref={panelRef}
            tabIndex={-1}
            onClick={(e) => e.stopPropagation()}
          >
            <div className="cta-share-header">
              <span className="cta-share-title">{modalTitle}</span>
              <button className="cta-share-close" onClick={closeModal} aria-label="Close">
                <X size={14} />
              </button>
            </div>
            {staleNotice && (
              <div
                style={{
                  margin: "8px 12px",
                  padding: "7px 10px",
                  border: "1px solid var(--warn)",
                  borderRadius: "3px",
                  background: "color-mix(in srgb, var(--warn) 8%, transparent)",
                  fontFamily: "var(--font-mono, monospace)",
                  fontSize: "10px",
                  color: "var(--warn)",
                }}
              >
                {staleNotice}
              </div>
            )}
            <iframe
              className="cta-share-iframe"
              src={shareUrl}
              title={shareContentTitle}
              sandbox="allow-scripts allow-same-origin"
            />
          </div>
        </div>
      )}
    </>
  );
}
