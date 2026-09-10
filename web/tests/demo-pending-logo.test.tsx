/**
 * @vitest-environment jsdom
 *
 * DemoPendingLogo renders a small WebGL "flow" mark on /demo-pending — a
 * public, unauthenticated leaf reachable on both demo.radon.run and
 * app.radon.run (pinned by demo-provisioning-resilience.test.ts's
 * "/demo-pending perimeter" describe block). Decoration must never be the
 * thing standing between a visitor and the plain static card that shipped
 * before this component existed. These pin the two degrade paths: no WebGL
 * support (jsdom's real default — no mocking needed), and a visitor's
 * prefers-reduced-motion preference.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { ThemeProvider } from "@/lib/ThemeContext";
import DemoPendingLogo from "@/app/demo-pending/DemoPendingLogo";

let container: HTMLDivElement;
let root: Root;

function mockMatchMedia(reduceMotion: boolean) {
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    configurable: true,
    value: (query: string) => ({
      matches: query.includes("prefers-reduced-motion") ? reduceMotion : false,
      media: query,
      onchange: null,
      addEventListener: () => {},
      removeEventListener: () => {},
      addListener: () => {},
      removeListener: () => {},
      dispatchEvent: () => false,
    }),
  });
}

beforeEach(() => {
  container = document.createElement("div");
  document.body.appendChild(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  vi.restoreAllMocks();
});

describe("DemoPendingLogo degrade paths", () => {
  it("renders the empty mount (static card, no canvas) when WebGL is unavailable", async () => {
    mockMatchMedia(false);
    root = createRoot(container);
    await act(async () => {
      root.render(
        <ThemeProvider>
          <DemoPendingLogo />
        </ThemeProvider>,
      );
    });

    const mount = container.querySelector(".demo-pending-logo");
    expect(mount).not.toBeNull();
    // jsdom implements HTMLCanvasElement but not a WebGL context — getContext
    // returns null, so the component must not have appended a canvas.
    expect(mount!.childElementCount).toBe(0);
    expect(mount!.querySelector("canvas")).toBeNull();
  });

  it("never probes for a WebGL context when prefers-reduced-motion is set", async () => {
    mockMatchMedia(true);
    const getContextSpy = vi.spyOn(HTMLCanvasElement.prototype, "getContext");

    root = createRoot(container);
    await act(async () => {
      root.render(
        <ThemeProvider>
          <DemoPendingLogo />
        </ThemeProvider>,
      );
    });

    expect(getContextSpy).not.toHaveBeenCalled();
    expect(container.querySelector(".demo-pending-logo")!.childElementCount).toBe(0);
  });

  it("cleans up its canvas and animation frame on unmount without throwing", async () => {
    mockMatchMedia(false);
    // Simulate WebGL support: jsdom's canvas has no real GL, so stub a
    // minimal-but-functional context covering every call the component makes.
    const uniforms = new Map<string, unknown>();
    const fakeGl = {
      VERTEX_SHADER: 1,
      FRAGMENT_SHADER: 2,
      COMPILE_STATUS: 3,
      LINK_STATUS: 4,
      ARRAY_BUFFER: 5,
      STATIC_DRAW: 6,
      TRIANGLES: 7,
      COLOR_BUFFER_BIT: 8,
      FLOAT: 9,
      SRC_ALPHA: 10,
      ONE_MINUS_SRC_ALPHA: 11,
      BLEND: 12,
      createShader: () => ({}),
      shaderSource: () => {},
      compileShader: () => {},
      getShaderParameter: () => true,
      deleteShader: () => {},
      createProgram: () => ({}),
      attachShader: () => {},
      linkProgram: () => {},
      getProgramParameter: () => true,
      deleteProgram: () => {},
      useProgram: () => {},
      createBuffer: () => ({}),
      bindBuffer: () => {},
      bufferData: () => {},
      deleteBuffer: () => {},
      getAttribLocation: () => 0,
      enableVertexAttribArray: () => {},
      vertexAttribPointer: () => {},
      getUniformLocation: (_: unknown, name: string) => {
        uniforms.set(name, {});
        return uniforms.get(name);
      },
      uniform1f: () => {},
      uniform2f: () => {},
      uniform3f: () => {},
      viewport: () => {},
      clearColor: () => {},
      enable: () => {},
      blendFunc: () => {},
      clear: () => {},
      drawArrays: () => {},
    };
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(
      fakeGl as unknown as WebGLRenderingContext,
    );

    root = createRoot(container);
    await act(async () => {
      root.render(
        <ThemeProvider>
          <DemoPendingLogo />
        </ThemeProvider>,
      );
    });

    const mount = container.querySelector(".demo-pending-logo");
    expect(mount!.querySelector("canvas")).not.toBeNull();

    await expect(act(async () => root.unmount())).resolves.not.toThrow();
    // afterEach's own unmount would double-unmount; give it an empty root.
    root = createRoot(document.createElement("div"));
  });
});
