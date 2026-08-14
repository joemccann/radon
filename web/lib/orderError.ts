export type FormattedOrderError = {
  summary: string;
  details: string[];
};

function normaliseLineBreaks(message: string): string {
  // IBKR rejection text embeds soft breaks as literal "<br>" tokens.
  // Convert every variant to a real newline before whitespace collapse so the
  // detail row can render with `white-space: pre-line` instead of leaking
  // angle-bracket text into the UI.
  return message.replace(/<br\s*\/?>/gi, "\n");
}

function collapseHorizontalWhitespace(message: string): string {
  // Preserve newlines so the line-break normalisation above survives, but
  // squash runs of spaces/tabs that IB tends to leave around the breaks.
  return message
    .replace(/[ \t]+/g, " ")
    .replace(/ ?\n ?/g, "\n")
    .replace(/\n{2,}/g, "\n")
    .trim();
}

function stripTransportWrappers(message: string): string {
  const withRealBreaks = normaliseLineBreaks(message);
  return collapseHorizontalWhitespace(
    withRealBreaks
      .replace(/^Radon API \d+:\s*/i, "")
      .replace(/^IB error \d+:\s*/i, ""),
  );
}

function stripRejectedPrefix(message: string): string {
  return message
    .replace(/^Order rejected by IB:\s*/i, "")
    .replace(/^Order rejected\s*-\s*reason:\s*/i, "")
    .replace(/^Order rejected:\s*/i, "")
    .trim();
}

function formatUsdNumber(value: string): string {
  const normalized = Number(value.replace(/,/g, ""));
  if (!Number.isFinite(normalized)) return value;
  return normalized.toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

export function formatOrderError(message: string | null | undefined): FormattedOrderError {
  const raw = String(message ?? "").trim();
  if (!raw) {
    return { summary: "Order placement failed.", details: [] };
  }

  const cleaned = stripTransportWrappers(raw);
  const rejectedReason = stripRejectedPrefix(cleaned);

  if (/network error placing order/i.test(cleaned)) {
    return { summary: "Network error while placing order.", details: [] };
  }

  if (/^Cancelled$/i.test(rejectedReason)) {
    return { summary: "Order rejected by IB.", details: ["Cancelled."] };
  }

  if (/no acknowledgement/i.test(rejectedReason)) {
    return { summary: "Order was not acknowledged by IB.", details: [] };
  }

  if (/trade not found/i.test(cleaned) || /no longer working at ib/i.test(cleaned)) {
    return {
      summary: "Order is no longer working at IB.",
      details: /day order/i.test(cleaned)
        ? ["DAY orders end at the regular-session close."]
        : ["It may have filled or been cancelled."],
    };
  }

  if (
    /YOUR ORDER IS NOT ACCEPTED/i.test(rejectedReason) &&
    /PREVIOUS DAY EQUITY WITH LOAN VALUE/i.test(rejectedReason) &&
    /INITIAL MARGIN/i.test(rejectedReason)
  ) {
    const usdValues = [...rejectedReason.matchAll(/([\d,.]+)\s*USD/gi)].map((match) => match[1]);
    if (usdValues.length >= 2) {
      return {
        summary: "Order rejected by IB: insufficient margin.",
        details: [
          `Previous-day equity with loan value is $${formatUsdNumber(usdValues[0])}; initial margin required is $${formatUsdNumber(usdValues[1])}.`,
        ],
      };
    }
    return {
      summary: "Order rejected by IB: insufficient margin.",
      details: [],
    };
  }

  if (rejectedReason && rejectedReason !== cleaned) {
    return {
      summary: "Order rejected by IB.",
      details: [rejectedReason.endsWith(".") ? rejectedReason : `${rejectedReason}.`],
    };
  }

  return {
    summary: cleaned.endsWith(".") ? cleaned : `${cleaned}.`,
    details: [],
  };
}

export function formatOrderErrorMessage(message: string | null | undefined): string {
  const formatted = formatOrderError(message);
  return [formatted.summary, ...formatted.details].join(" ");
}
