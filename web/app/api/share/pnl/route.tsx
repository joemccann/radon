import { ImageResponse } from "next/og";
import { loadFonts } from "@/lib/og-fonts";
import { OG } from "@/lib/og-theme";

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

export async function GET(request: Request) {
  try {
    const { searchParams } = new URL(request.url);
    const description = searchParams.get("description") ?? "";
    const pnl = parseFloat(searchParams.get("pnl") ?? "0");
    const pnlPctRaw = searchParams.get("pnlPct");
    const pnlPct = pnlPctRaw != null ? parseFloat(pnlPctRaw) : null;
    const commRaw = searchParams.get("commission");
    const commission = commRaw != null ? parseFloat(commRaw) : null;
    const fillPriceRaw = searchParams.get("fillPrice");
    const fillPrice = fillPriceRaw != null ? parseFloat(fillPriceRaw) : null;
    const time = searchParams.get("time") ?? "";

    if (!description) {
      return new Response("Missing description", { status: 400 });
    }

    const fonts = await loadFonts();
    const isPositive = pnl >= 0;
    const accentColor = isPositive ? OG.positive : OG.negative;

    const detailItems: { label: string; value: string }[] = [];
    if (fillPrice != null && Number.isFinite(fillPrice)) {
      detailItems.push({ label: "FILL", value: `$${fillPrice.toFixed(2)}` });
    }
    if (commission != null && Number.isFinite(commission)) {
      detailItems.push({ label: "COMMISSION", value: `$${Math.abs(commission).toFixed(2)}` });
    }
    if (time) {
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
          {/* Main content area */}
          <div
            style={{
              display: "flex",
              flexDirection: "column",
              flexGrow: 1,
              padding: "56px 64px 0 64px",
            }}
          >
            {/* Contract description */}
            <div
              style={{
                display: "flex",
                fontSize: "24px",
                fontWeight: 400,
                color: OG.muted,
                marginBottom: "20px",
              }}
            >
              {description}
            </div>

            {/* Hero P&L dollar amount + pct */}
            <div
              style={{
                display: "flex",
                flexDirection: "row",
                alignItems: "baseline",
                marginBottom: "12px",
              }}
            >
              <span
                style={{
                  fontSize: "80px",
                  fontWeight: 700,
                  color: accentColor,
                  lineHeight: "1",
                }}
              >
                {fmtDollar(pnl)}
              </span>
              {pnlPct != null && Number.isFinite(pnlPct) ? (
                <span
                  style={{
                    fontSize: "40px",
                    fontWeight: 700,
                    color: accentColor,
                    opacity: 0.75,
                    lineHeight: "1",
                    marginLeft: "24px",
                  }}
                >
                  {fmtPct(pnlPct)}
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
                marginBottom: "40px",
              }}
            />

            {/* Detail items row */}
            <div style={{ display: "flex", flexDirection: "row" }}>
              {detailItems.map((item, idx) => (
                <div
                  key={idx}
                  style={{
                    display: "flex",
                    flexDirection: "column",
                    marginRight: idx < detailItems.length - 1 ? "56px" : "0px",
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
