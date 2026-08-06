import InstrumentSkeleton from "@/components/ui/InstrumentSkeleton";

/**
 * Route-level loading UI for /scanner while the workspace chunk streams.
 * Matte instrument panel — never a blank content hole (skill-stack T6).
 */
export default function ScannerLoading() {
  return (
    <div className="scanner-page-shell" data-testid="scanner-route-loading">
      <InstrumentSkeleton testId="scanner-route-skeleton" />
    </div>
  );
}
