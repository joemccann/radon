"use client";

import React, { useState } from "react";
import { resolvePublisher, PublisherBrand } from "@/lib/publisherIcons";
import styles from "./PublisherLogo.module.css";

export type PublisherLogoProps = {
  publisher?: string | null;
  size?: number;
  className?: string;
  showLabel?: boolean;
  labelClassName?: string;
  showType?: boolean;
  "aria-hidden"?: boolean | "true" | "false";
};

export default function PublisherLogo({
  publisher,
  size = 16,
  className,
  showLabel = false,
  labelClassName,
  showType = false,
  "aria-hidden": ariaHidden,
}: PublisherLogoProps) {
  const brand: PublisherBrand = resolvePublisher(publisher);
  const [hasError, setHasError] = useState(false);

  const isFallback = brand.isFallback || hasError;

  const content = (
    <span
      data-testid="publisher-logo"
      data-publisher-id={isFallback && hasError ? "default" : brand.id}
      data-is-fallback={isFallback ? "true" : "false"}
      className={`${styles.iconWrapper} ${className ?? ""}`}
      style={{ width: size, height: size }}
      title={showLabel ? undefined : brand.name}
    >
      {isFallback ? (
        <svg
          className={styles.fallbackSvg}
          width={size}
          height={size}
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.5"
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden={ariaHidden ?? true}
        >
          <path d="M4 4a2 2 0 0 1 2-2h8l6 6v12a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V4z" />
          <path d="M14 2v6h6" />
          <path d="M8 13h8M8 17h5" />
        </svg>
      ) : (
        /* eslint-disable-next-line @next/next/no-img-element */
        <img
          src={brand.iconUrl}
          alt={brand.name}
          width={size}
          height={size}
          className={styles.icon}
          onError={() => setHasError(true)}
          aria-hidden={ariaHidden ?? true}
        />
      )}
    </span>
  );

  if (!showLabel) {
    return content;
  }

  return (
    <div className={`${styles.badge} ${className ?? ""}`} data-testid="publisher-badge">
      {content}
      <span className={`${styles.label} ${labelClassName ?? ""}`}>
        {publisher?.trim() || brand.name}
      </span>
      {showType ? <span className={styles.typeBadge}>Research</span> : null}
    </div>
  );
}
