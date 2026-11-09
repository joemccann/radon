"use client";
import ErrorToast from "@/components/ErrorToast";
import { FileDown } from "lucide-react";
import { useRef, useState } from "react";
import { buildArticlePdf } from "@/lib/newsfeedPdf";
import type { SharePost } from "@/lib/newsfeedShare";
import { savePdfFile, type SaveOutcome } from "@/lib/savePdfFile";
import { userErrorMessage } from "@/lib/userError";
import styles from "./NewsfeedShare.module.css";

type Prepared = { file: File; missingFigures: number };

export default function NewsfeedPdfExport({ post }: { post: SharePost }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const prepared = useRef<Prepared | null>(null);
  const [ready, setReady] = useState(false);

  async function exportPdf() {
    if (busy) return;
    setError(""); setMessage("");
    if (prepared.current) return deliver(prepared.current);
    setBusy(true);
    try {
      const pdf = await buildArticlePdf(post);
      prepared.current = pdf;
      await deliver(pdf);
    } catch (err) {
      setError(userErrorMessage(err, "PDF export failed. Try again."));
    } finally { setBusy(false); }
  }

  async function deliver(pdf: Prepared) {
    const outcome = await savePdfFile(pdf.file);
    setReady(outcome === "needs-gesture" || outcome === "cancelled");
    setMessage(statusFor(outcome, pdf.missingFigures));
  }

  return <div className={styles.root} data-newsfeed-pdf>
    <button type="button" className={styles.trigger} disabled={busy} aria-busy={busy}
      aria-label={ready ? "Save PDF" : "Export PDF"} onClick={() => void exportPdf()}>
      <FileDown size={15} aria-hidden /> {busy ? "Building PDF…" : ready ? "Save PDF" : "PDF"}
    </button>
    {error ? <ErrorToast message={error} onRetry={() => void exportPdf()} /> : null}
    {message ? <p role="status" className={styles.status}>{message}</p> : null}
  </div>;
}

function statusFor(outcome: SaveOutcome, missingFigures: number): string {
  const missing = missingFigures ? ` ${missingFigures} chart${missingFigures === 1 ? "" : "s"} could not be loaded and ${missingFigures === 1 ? "is" : "are"} marked in the PDF.` : "";
  if (outcome === "needs-gesture") return `PDF ready. Tap Save PDF to save it to Files.${missing}`;
  if (outcome === "cancelled") return "";
  return `${outcome === "shared" ? "PDF shared." : "PDF downloaded."}${missing}`;
}
