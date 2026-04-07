import { MonoMetric } from "@/components/atoms/MonoMetric";
import { TelemetryLabel } from "@/components/atoms/TelemetryLabel";
import type { SurfaceItem } from "@/lib/landing-content";

export function SurfacePanelStack({ item }: { item: SurfaceItem }) {
  return (
    <article className="border border-grid bg-panel transition-colors duration-200 hover:border-accent/40">
      <div className="border-b border-grid px-5 py-4">
        <TelemetryLabel tone="core">{item.name}</TelemetryLabel>
        <h3 className="mt-3 font-sans text-xl font-semibold text-primary">
          {item.headline}
        </h3>
      </div>
      <div className="grid border-b border-grid sm:grid-cols-2">
        {item.metrics.map((metric, index) => (
          <div key={metric.label} className={`min-w-0 bg-panel ${index > 0 ? "border-t border-grid sm:border-t-0 sm:border-l" : ""}`}>
            <MonoMetric
              label={metric.label}
              value={metric.value}
              tone={index === 0 ? "core" : "primary"}
              size="compact"
            />
          </div>
        ))}
      </div>
      <div className="px-5 py-5">
        <ul className="space-y-3 text-sm leading-6 text-secondary">
          {item.details.map((detail) => (
            <li key={detail}>{detail}</li>
          ))}
        </ul>
      </div>
    </article>
  );
}
