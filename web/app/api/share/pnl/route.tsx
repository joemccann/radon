import { ImageResponse } from "next/og";
import { loadFonts } from "@/lib/og-fonts";
import { OG } from "@/lib/og-theme";
import { rateLimit, clientIp, SHARE_PNL_LIMIT, SHARE_WINDOW_MS } from "@/lib/rateLimit";

export const runtime = "nodejs";

function fmtDollar(v: number): string {
  const abs = Math.abs(v);
  const sign = v >= 0 ? "+" : "-";
  return `${sign}$${abs.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function fmtPct(v: number): string {
  const sign = v >= 0 ? "+" : "";
  return `${sign}${v.toFixed(2)}%`;
}

/** Format ISO timestamp to PST with date and time.
 *  Date-only strings (e.g., "2026-03-09" from trade_log) render as
 *  date only — no fake timestamp. */
function fmtTimePST(isoTime: string): string {
  try {
    // Date-only (no "T" separator) → display as date without time
    if (/^\d{4}-\d{2}-\d{2}$/.test(isoTime.trim())) {
      const [y, m, d] = isoTime.split("-");
      return `${parseInt(m)}/${parseInt(d)}/${y}`;
    }
    const date = new Date(isoTime);
    if (Number.isNaN(date.getTime())) return "";
    return date.toLocaleString("en-US", {
      timeZone: "America/Los_Angeles",
      month: "numeric",
      day: "numeric",
      year: "numeric",
      hour: "numeric",
      minute: "2-digit",
      hour12: true,
    }) + " PST";
  } catch {
    return "";
  }
}

export async function GET(request: Request) {
  const limit = rateLimit(clientIp(request), { limit: SHARE_PNL_LIMIT, windowMs: SHARE_WINDOW_MS });
  if (!limit.ok) {
    return new Response("Too Many Requests", {
      status: 429,
      headers: { "Retry-After": String(limit.retryAfterSec) },
    });
  }

  try {
    const { searchParams } = new URL(request.url);
    const description = searchParams.get("description") ?? "";
    const pnlRaw = searchParams.get("pnl");
    const pnl = pnlRaw != null ? parseFloat(pnlRaw) : null;
    const pnlPctRaw = searchParams.get("pnlPct");
    const pnlPct = pnlPctRaw != null ? parseFloat(pnlPctRaw) : null;
    const fillPriceRaw = searchParams.get("fillPrice");
    const fillPrice = fillPriceRaw != null ? parseFloat(fillPriceRaw) : null;
    const entryPriceRaw = searchParams.get("entryPrice");
    const entryPrice = entryPriceRaw != null ? parseFloat(entryPriceRaw) : null;
    const exitPriceRaw = searchParams.get("exitPrice");
    const exitPrice = exitPriceRaw != null ? parseFloat(exitPriceRaw) : null;
    const entryTime = searchParams.get("entryTime") ?? "";
    const exitTime = searchParams.get("exitTime") ?? "";
    const holdTime = searchParams.get("holdTime") ?? "";
    const time = searchParams.get("time") ?? "";

    if (!description) {
      return new Response("Missing description", { status: 400 });
    }

    const fonts = await loadFonts();

    // Determine accent color from whichever value is present
    const refValue = pnl ?? pnlPct ?? 0;
    const isPositive = refValue >= 0;
    const accentColor = isPositive ? OG.positive : OG.negative;

    // Build hero text parts
    const heroDollar = pnl != null && Number.isFinite(pnl) ? fmtDollar(pnl) : null;
    const heroPct = pnlPct != null && Number.isFinite(pnlPct) ? fmtPct(pnlPct) : null;

    const fmtSignedPrice = (v: number): string =>
      v < 0 ? `-$${Math.abs(v).toFixed(2)}` : `$${v.toFixed(2)}`;

    const detailItems: { label: string; value: string }[] = [];

    // Entry: price @ time (PST)
    if (entryPrice != null && Number.isFinite(entryPrice)) {
      const entryTimeFormatted = entryTime ? fmtTimePST(entryTime) : "";
      const entryValue = entryTimeFormatted
        ? `${fmtSignedPrice(entryPrice)} @ ${entryTimeFormatted}`
        : fmtSignedPrice(entryPrice);
      detailItems.push({ label: "ENTRY", value: entryValue });
    }

    // Hold time: entry to exit in natural language, centered between ENTRY and EXIT
    if (holdTime) {
      detailItems.push({ label: "HELD", value: holdTime });
    }

    // Exit: price @ time (PST)
    if (exitPrice != null && Number.isFinite(exitPrice)) {
      const exitTimeFormatted = exitTime ? fmtTimePST(exitTime) : "";
      const exitValue = exitTimeFormatted
        ? `${fmtSignedPrice(exitPrice)} @ ${exitTimeFormatted}`
        : fmtSignedPrice(exitPrice);
      detailItems.push({ label: "EXIT", value: exitValue });
    }

    // Fallback: single fill price (no entry/exit)
    if (entryPrice == null && exitPrice == null && fillPrice != null && Number.isFinite(fillPrice)) {
      detailItems.push({ label: "FILL", value: `$${fillPrice.toFixed(2)}` });
    }

    // Note: Commission is intentionally NOT included — it clutters the card

    // Legacy: show executed time if no entry/exit times provided
    if (time && !entryTime && !exitTime) {
      detailItems.push({ label: "EXECUTED", value: time });
    }

    return new ImageResponse(
      (
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            width: "1200px",
            height: "630px",
            background: OG.bg,
            fontFamily: "IBM Plex Mono",
            color: OG.text,
          }}
        >
          {/* Main content area — fills the space */}
          <div
            style={{
              display: "flex",
              flexDirection: "column",
              flexGrow: 1,
              padding: "48px 64px 40px 64px",
            }}
          >
            {/* Top: Contract description — left-aligned */}
            <div
              style={{
                display: "flex",
                fontSize: "24px",
                fontWeight: 400,
                color: OG.muted,
              }}
            >
              {description}
            </div>

            {/* Center: Hero P&L — grows to fill vertical space */}
            <div
              style={{
                display: "flex",
                flexDirection: "column",
                alignItems: "center",
                justifyContent: "center",
                flexGrow: 1,
              }}
            >
              <div
                style={{
                  display: "flex",
                  flexDirection: "row",
                  alignItems: "baseline",
                  justifyContent: "center",
                }}
              >
                {heroDollar ? (
                  <span
                    style={{
                      fontSize: heroPct ? "92px" : "192px",
                      fontWeight: 700,
                      color: accentColor,
                      lineHeight: "1",
                    }}
                  >
                    {heroDollar}
                  </span>
                ) : null}
                {heroPct ? (
                  <span
                    style={{
                      fontSize: heroDollar ? "86px" : "192px",
                      fontWeight: 700,
                      color: accentColor,
                      opacity: heroDollar ? 0.75 : 1,
                      lineHeight: "1",
                      marginLeft: heroDollar ? "18px" : "0",
                    }}
                  >
                    {heroPct}
                  </span>
                ) : null}
              </div>
              {/* Accent bar */}
              <div
                style={{
                  display: "flex",
                  width: "72px",
                  height: "3px",
                  background: accentColor,
                  marginTop: "16px",
                }}
              />
            </div>

            {/* Bottom: Detail items — spread across full width */}
            {detailItems.length > 0 ? (
              <div style={{ display: "flex", flexDirection: "row", justifyContent: "space-between" }}>
                {detailItems.map((item, idx) => (
                  <div
                    key={idx}
                    style={{
                      display: "flex",
                      flexDirection: "column",
                      alignItems: idx === 0 ? "flex-start" : idx === detailItems.length - 1 ? "flex-end" : "center",
                    }}
                  >
                    <span
                      style={{
                        color: OG.muted,
                        fontSize: "12px",
                        fontWeight: 700,
                        letterSpacing: "0.1em",
                        marginBottom: "6px",
                      }}
                    >
                      {item.label}
                    </span>
                    <span style={{ color: OG.text, fontSize: "16px" }}>
                      {item.value}
                    </span>
                  </div>
                ))}
              </div>
            ) : null}
          </div>

          {/* Bottom bar: Radon branding */}
          <div
            style={{
              display: "flex",
              flexDirection: "row",
              alignItems: "center",
              justifyContent: "space-between",
              padding: "28px 64px",
              borderTop: `1px solid ${OG.border}`,
            }}
          >
            <div style={{ display: "flex", flexDirection: "row", alignItems: "center" }}>
              {/* Radon icon: concentric circles */}
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  width: "40px",
                  height: "40px",
                  borderRadius: "50%",
                  border: "2px solid #05AD98",
                  marginRight: "16px",
                }}
              >
                <div
                  style={{
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    width: "24px",
                    height: "24px",
                    borderRadius: "50%",
                    border: "1.5px solid #048A7A",
                  }}
                >
                  <div
                    style={{
                      display: "flex",
                      width: "6px",
                      height: "6px",
                      borderRadius: "50%",
                      background: OG.text,
                    }}
                  />
                </div>
              </div>
              <div style={{ display: "flex", flexDirection: "column" }}>
                <span style={{ fontSize: "20px", fontWeight: 700, color: OG.text, letterSpacing: "0.12em" }}>
                  RADON
                </span>
                <span style={{ fontSize: "10px", fontWeight: 500, color: "#05AD98", letterSpacing: "0.15em" }}>
                  TERMINAL
                </span>
              </div>
            </div>
            <span style={{ fontSize: "14px", color: OG.muted, fontWeight: 400 }}>
              Executed with Radon
            </span>
          </div>
        </div>
      ),
      {
        width: 1200,
        height: 630,
        fonts: fonts as any,
      },
    );
  } catch (err) {
    console.error("Share PnL image generation failed:", err);
    return new Response(
      `Image generation failed: ${err instanceof Error ? err.message : "Unknown error"}`,
      { status: 500 },
    );
  }
}
