/**
 * libraries.dev pack C — consumer renders for the site FX constants.
 *
 * Each constant is asserted at the component that spreads it (CtaBeam,
 * HeroBeam, MarkerAccent), not by restating the literal next to itself:
 * the border-beam / metal-fx stubs reflect the props they receive into
 * data attributes, so deleting a `{...CONSTANT}` spread reds this file.
 *
 * Runs under the ROOT vitest.config.ts (include: site/lib/**\/*.test.ts);
 * its site-app-alias plugin resolves the components' "@/lib" imports to
 * site/lib. Server render keeps everything on site/node_modules' React.
 */
import { createElement, type ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import { CTA_BEAM, HERO_BEAM, MARKER_METAL } from "./librariesFx";
import { DEFAULT_SITE_THEME } from "./theme";
import { CtaBeam } from "../components/atoms/CtaBeam";
import { HeroBeam } from "../components/atoms/HeroBeam";
import { MarkerAccent } from "../components/atoms/MarkerAccent";

type BeamStubProps = {
  children?: ReactNode;
  className?: string;
  theme?: string;
  size?: string;
  colorVariant?: string;
  staticColors?: boolean;
  strength?: number;
  borderRadius?: number;
  "data-testid"?: string;
};

vi.mock("border-beam", async () => {
  const { createElement: h } = await import("react");
  return {
    BorderBeam: (props: BeamStubProps) =>
      h(
        "div",
        {
          "data-testid": props["data-testid"] ?? "border-beam",
          className: props.className,
          "data-theme": props.theme,
          "data-size": String(props.size),
          "data-color-variant": String(props.colorVariant),
          "data-static-colors": String(props.staticColors),
          "data-strength": String(props.strength),
          "data-border-radius": String(props.borderRadius),
        },
        props.children,
      ),
  };
});

type MetalStubProps = {
  children?: ReactNode;
  theme?: string;
  preset?: string;
  variant?: string;
  strength?: number;
  "data-testid"?: string;
};

vi.mock("metal-fx", async () => {
  const { createElement: h } = await import("react");
  return {
    MetalFx: (props: MetalStubProps) =>
      h(
        "span",
        {
          "data-testid": props["data-testid"] ?? "metal-fx",
          "data-theme": props.theme,
          "data-preset": String(props.preset),
          "data-variant": String(props.variant),
          "data-strength": String(props.strength),
        },
        props.children,
      ),
  };
});

function attrsOf(html: string, testid: string): Record<string, string> {
  const tag = html.match(new RegExp(`<[^>]*data-testid="${testid}"[^>]*>`))?.[0];
  expect(tag, `element ${testid} in ${html}`).toBeTruthy();
  const attrs: Record<string, string> = {};
  for (const [, name, value] of tag!.matchAll(/([\w-]+)="([^"]*)"/g)) {
    attrs[name] = value;
  }
  return attrs;
}

describe("CtaBeam spreads CTA_BEAM onto the beam", () => {
  const html = renderToStaticMarkup(createElement(CtaBeam, null, "Start"));
  const beam = attrsOf(html, "cta-beam");

  it("renders a mono, static, small beam (not a rainbow idle palette)", () => {
    expect(beam["data-color-variant"]).toBe("mono");
    expect(beam["data-static-colors"]).toBe("true");
    expect(beam["data-size"]).toBe("sm");
  });

  it("passes the calm strength and panel radius through", () => {
    expect(beam["data-strength"]).toBe(String(CTA_BEAM.strength));
    expect(Number(beam["data-strength"])).toBeLessThan(0.7);
    expect(beam["data-border-radius"]).toBe(String(CTA_BEAM.borderRadius));
  });

  it("wraps its children and carries the site theme", () => {
    expect(html).toContain("Start");
    expect(beam["data-theme"]).toBe(DEFAULT_SITE_THEME);
    expect(beam.class).toBe("cta-beam");
  });
});

describe("HeroBeam spreads HERO_BEAM onto the beam", () => {
  const html = renderToStaticMarkup(createElement(HeroBeam, null, "Plate"));
  const beam = attrsOf(html, "hero-beam");

  it("keeps a single calm mono line beam for the flow plate", () => {
    expect(beam["data-color-variant"]).toBe("mono");
    expect(beam["data-static-colors"]).toBe("true");
    expect(beam["data-size"]).toBe("line");
    expect(beam["data-strength"]).toBe(String(HERO_BEAM.strength));
    expect(Number(beam["data-strength"])).toBeLessThan(0.7);
  });

  it("wraps its children and carries the site theme", () => {
    expect(html).toContain("Plate");
    expect(beam["data-theme"]).toBe(DEFAULT_SITE_THEME);
  });
});

describe("MarkerAccent spreads MARKER_METAL onto the metal accent", () => {
  const html = renderToStaticMarkup(createElement(MarkerAccent, null, "Gate"));
  const metal = attrsOf(html, "marker-accent");

  it("uses one light silver metal button system", () => {
    expect(metal["data-preset"]).toBe("silver");
    expect(metal["data-variant"]).toBe("button");
    expect(metal["data-strength"]).toBe(String(MARKER_METAL.strength));
    expect(Number(metal["data-strength"])).toBeLessThan(0.5);
  });

  it("wraps its children and carries the site theme", () => {
    expect(html).toContain("Gate");
    expect(metal["data-theme"]).toBe(DEFAULT_SITE_THEME);
  });
});
