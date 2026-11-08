export type SaveOutcome = "shared" | "downloaded" | "cancelled" | "needs-gesture";

/**
 * Phones (including the installed PWA, where blob downloads are unreliable) get the
 * system share sheet, which offers Save to Files. Desktop browsers download directly.
 */
export async function savePdfFile(file: File): Promise<SaveOutcome> {
  if (!prefersShareSheet(file)) {
    downloadFile(file);
    return "downloaded";
  }
  try {
    await navigator.share({ files: [file], title: file.name });
    return "shared";
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") return "cancelled";
    // Safari drops the user gesture during a long build; a second tap shares immediately.
    if (error instanceof DOMException && error.name === "NotAllowedError") return "needs-gesture";
    downloadFile(file);
    return "downloaded";
  }
}

export function prefersShareSheet(file: File): boolean {
  if (typeof navigator.share !== "function" || typeof navigator.canShare !== "function") return false;
  if (!navigator.canShare({ files: [file] })) return false;
  return typeof window.matchMedia === "function" && window.matchMedia("(pointer: coarse)").matches;
}

function downloadFile(file: File): void {
  const url = URL.createObjectURL(file);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = file.name;
  anchor.rel = "noopener";
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  setTimeout(() => URL.revokeObjectURL(url), 30_000);
}
