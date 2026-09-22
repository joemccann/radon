/**
 * @vitest-environment jsdom
 *
 * T-450: light-mode behaviour of ThemeProvider's prefers-color-scheme read.
 *
 * The global vitest.setup.ts matchMedia shim defaults to
 * `matches: query.includes("dark")`, so "(prefers-color-scheme: dark)" was
 * always true and a conditional `if (!window.matchMedia)` install in a test
 * file (the pattern used by theme-provider-hydration.test.tsx) is dead code —
 * the setup beforeEach already occupied the slot. `setMatchMedia` is the
 * sanctioned per-test override; this file asserts the light branch of
 * `readClientTheme` is reachable through it, and that the dark default
 * comes back untouched for the next test.
 */
import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { ThemeProvider, useTheme } from "@/lib/ThemeContext";
import { setMatchMedia } from "../../vitest.setup";

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  container = document.createElement("div");
  document.body.appendChild(container);
});

afterEach(() => {
  act(() => {
    root?.unmount();
  });
  container.remove();
  document.documentElement.removeAttribute("data-theme");
  window.localStorage.clear();
});

function ThemeProbe({ onTheme }: { onTheme: (t: string) => void }) {
  const { theme } = useTheme();
  onTheme(theme);
  return <span data-testid="probe">{theme}</span>;
}

function renderProbe(): string[] {
  const captured: string[] = [];
  act(() => {
    root = createRoot(container);
    root.render(
      <ThemeProvider>
        <ThemeProbe onTheme={(t) => captured.push(t)} />
      </ThemeProvider>,
    );
  });
  return captured;
}

describe("ThemeProvider light-mode preference", () => {
  it("resolves to 'light' after mount when the OS prefers light and no theme is stored", () => {
    setMatchMedia((query) => !query.includes("dark"));
    const captured = renderProbe();

    // First render stays pinned to the SSR default (hydration safety)...
    expect(captured[0]).toBe("dark");
    // ...then the post-mount read of prefers-color-scheme lands on light.
    expect(captured.at(-1)).toBe("light");
    expect(document.documentElement.getAttribute("data-theme")).toBe("light");
  });

  it("keeps the dark default when no override is set (backward compatibility)", () => {
    const captured = renderProbe();
    expect(captured.at(-1)).toBe("dark");
  });
});
