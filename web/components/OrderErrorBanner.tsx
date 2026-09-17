"use client";

import ErrorToast from "@/components/ErrorToast";
import { formatOrderError } from "@/lib/orderError";

type OrderErrorBannerProps = {
  error: string | null;
};

export default function OrderErrorBanner({ error }: OrderErrorBannerProps) {
  if (!error) return null;

  const formatted = formatOrderError(error);

  return (
    <ErrorToast message={<>
      <div className="order-error-summary">{formatted.summary}</div>
      {formatted.details.map((detail) => (
        <div key={detail} className="order-error-detail">
          {detail}
        </div>
      ))}
    </>} />
  );
}
