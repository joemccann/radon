import { describe, it, expect } from "vitest";
import { resolvePublisher, DEFAULT_FALLBACK_ICON } from "../lib/publisherIcons";

describe("resolvePublisher", () => {
  it("resolves Goldman Sachs and desk variants", () => {
    expect(resolvePublisher("Goldman Sachs")).toEqual({
      id: "goldman-sachs",
      name: "Goldman Sachs",
      iconUrl: "/icons/publishers/goldman-sachs.svg",
      isFallback: false,
    });
    expect(resolvePublisher("Goldman Sachs Global Investment Research")?.id).toBe("goldman-sachs");
    expect(resolvePublisher("Goldman Sachs Equity Research")?.id).toBe("goldman-sachs");
    expect(resolvePublisher("Goldman Sachs FICC & Equities")?.id).toBe("goldman-sachs");
    expect(resolvePublisher("Goldman Sachs Economics (Boak)")?.id).toBe("goldman-sachs");
  });

  it("resolves Bank of America and BofA variants", () => {
    expect(resolvePublisher("Bank of America")).toEqual({
      id: "bank-of-america",
      name: "Bank of America",
      iconUrl: "/icons/publishers/bank-of-america.svg",
      isFallback: false,
    });
    expect(resolvePublisher("BofA Global Research")?.id).toBe("bank-of-america");
    expect(resolvePublisher("BofA Global Research Credit Strategy")?.id).toBe("bank-of-america");
    expect(resolvePublisher("Merrill Lynch")?.id).toBe("bank-of-america");
  });

  it("resolves J.P. Morgan and JPM variants", () => {
    expect(resolvePublisher("J.P. Morgan")).toEqual({
      id: "jpmorgan",
      name: "J.P. Morgan",
      iconUrl: "/icons/publishers/jpmorgan.svg",
      isFallback: false,
    });
    expect(resolvePublisher("JPMorgan")?.id).toBe("jpmorgan");
    expect(resolvePublisher("JPM US Market Intelligence")?.id).toBe("jpmorgan");
    expect(resolvePublisher("JPM Positioning Intelligence")?.id).toBe("jpmorgan");
    expect(resolvePublisher("J.P. Morgan Data Assets & Alpha")?.id).toBe("jpmorgan");
  });

  it("resolves Morgan Stanley", () => {
    expect(resolvePublisher("Morgan Stanley")).toEqual({
      id: "morgan-stanley",
      name: "Morgan Stanley",
      iconUrl: "/icons/publishers/morgan-stanley.svg",
      isFallback: false,
    });
    expect(resolvePublisher("Morgan Stanley Research")?.id).toBe("morgan-stanley");
  });

  it("resolves Deutsche Bank and variants", () => {
    expect(resolvePublisher("Deutsche Bank")).toEqual({
      id: "deutsche-bank",
      name: "Deutsche Bank",
      iconUrl: "/icons/publishers/deutsche-bank.svg",
      isFallback: false,
    });
    expect(resolvePublisher("Deutsche Bank Research")?.id).toBe("deutsche-bank");
  });

  it("resolves Citi and Citigroup variants", () => {
    expect(resolvePublisher("Citi Research")).toEqual({
      id: "citi",
      name: "Citi",
      iconUrl: "/icons/publishers/citi.svg",
      isFallback: false,
    });
    expect(resolvePublisher("Citigroup")?.id).toBe("citi");
    expect(resolvePublisher("Citibank")?.id).toBe("citi");
  });

  it("resolves UBS and variants", () => {
    expect(resolvePublisher("UBS")).toEqual({
      id: "ubs",
      name: "UBS",
      iconUrl: "/icons/publishers/ubs.svg",
      isFallback: false,
    });
    expect(resolvePublisher("UBS Global Research")?.id).toBe("ubs");
    expect(resolvePublisher("UBS Chief Investment Office GWM")?.id).toBe("ubs");
  });

  it("resolves Mizuho", () => {
    expect(resolvePublisher("Mizuho")).toEqual({
      id: "mizuho",
      name: "Mizuho",
      iconUrl: "/icons/publishers/mizuho.svg",
      isFallback: false,
    });
    expect(resolvePublisher("Mizuho Securities")?.id).toBe("mizuho");
  });

  it("resolves other major global banks and institutions", () => {
    expect(resolvePublisher("Barclays")?.id).toBe("barclays");
    expect(resolvePublisher("Nomura")?.id).toBe("nomura");
    expect(resolvePublisher("MUFG")?.id).toBe("mufg");
    expect(resolvePublisher("BNP Paribas")?.id).toBe("bnp-paribas");
    expect(resolvePublisher("Standard Chartered")?.id).toBe("standard-chartered");
    expect(resolvePublisher("RBC Capital Markets")?.id).toBe("rbc");
    expect(resolvePublisher("Canaccord Genuity")?.id).toBe("canaccord");
    expect(resolvePublisher("BTIG")?.id).toBe("btig");
    expect(resolvePublisher("Apollo")?.id).toBe("apollo");
    expect(resolvePublisher("Wells Fargo")?.id).toBe("wells-fargo");
    expect(resolvePublisher("Jefferies")?.id).toBe("jefferies");
  });

  it("falls back to default icon for unrecognized, unknown, or empty publishers", () => {
    const unrec = resolvePublisher("TS Lombard");
    expect(unrec).toEqual({
      id: "default",
      name: "TS Lombard",
      iconUrl: DEFAULT_FALLBACK_ICON,
      isFallback: true,
    });

    const unknown = resolvePublisher("unknown");
    expect(unknown).toEqual({
      id: "default",
      name: "Research Document",
      iconUrl: DEFAULT_FALLBACK_ICON,
      isFallback: true,
    });

    const empty = resolvePublisher("");
    expect(empty).toEqual({
      id: "default",
      name: "Research Document",
      iconUrl: DEFAULT_FALLBACK_ICON,
      isFallback: true,
    });

    const nullVal = resolvePublisher(null);
    expect(nullVal).toEqual({
      id: "default",
      name: "Research Document",
      iconUrl: DEFAULT_FALLBACK_ICON,
      isFallback: true,
    });
  });
});
