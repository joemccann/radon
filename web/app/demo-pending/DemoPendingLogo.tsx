"use client";

import { useEffect, useRef } from "react";
import { useTheme } from "@/lib/ThemeContext";

// Matches the resting size of the radon-monogram.svg brand mark
// (web/public/brand/radon-monogram.svg) so the panel keeps the same
// footprint whether the WebGL sketch renders or not.
const SIZE = 72;

const VERTEX_SRC = `
attribute vec2 aPosition;
void main() {
  gl_Position = vec4(aPosition, 0.0, 1.0);
}
`;

// Reprises the brand monogram's two concentric rings + orbiting flow mark
// (web/public/brand/radon-monogram.svg) as a live sketch: a slow breathing
// pulse on the rings and one signal orbiting the outer ring.
const FRAGMENT_SRC = `
precision mediump float;
uniform vec2 uResolution;
uniform float uTime;
uniform vec3 uColorCore;
uniform vec3 uColorAccent;

void main() {
  vec2 uv = (gl_FragCoord.xy - 0.5 * uResolution) / min(uResolution.x, uResolution.y);
  float r = length(uv);
  float breathe = 0.5 + 0.5 * sin(uTime * 1.2);

  float ring1 = smoothstep(0.03, 0.0, abs(r - (0.32 + 0.015 * breathe)));
  float ring2 = smoothstep(0.02, 0.0, abs(r - 0.20));

  float angle = atan(uv.y, uv.x);
  float orbitAngle = uTime * 1.4;
  float delta = mod(angle - orbitAngle + 3.14159265, 6.2831853) - 3.14159265;
  float orbit = smoothstep(0.4, 0.0, abs(delta)) * smoothstep(0.035, 0.0, abs(r - 0.32));

  vec3 color = uColorCore * (ring1 * 0.85) + uColorAccent * (ring2 * 0.55 + orbit * 0.9);
  float alpha = clamp(ring1 * 0.85 + ring2 * 0.55 + orbit, 0.0, 1.0);
  gl_FragColor = vec4(color, alpha);
}
`;

function prefersReducedMotion(): boolean {
  if (typeof window === "undefined" || !window.matchMedia) return false;
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

function hexToRgb01(hex: string): [number, number, number] {
  const clean = hex.trim().replace("#", "");
  if (clean.length !== 6) return [0, 0, 0];
  const num = parseInt(clean, 16);
  return [((num >> 16) & 255) / 255, ((num >> 8) & 255) / 255, (num & 255) / 255];
}

function readThemeColor(name: string, fallback: string): [number, number, number] {
  if (typeof document === "undefined") return hexToRgb01(fallback);
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return hexToRgb01(value || fallback);
}

function compileShader(
  gl: WebGLRenderingContext,
  type: number,
  source: string,
): WebGLShader | null {
  const shader = gl.createShader(type);
  if (!shader) return null;
  gl.shaderSource(shader, source);
  gl.compileShader(shader);
  if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
    gl.deleteShader(shader);
    return null;
  }
  return shader;
}

function createProgram(gl: WebGLRenderingContext): WebGLProgram | null {
  const vertex = compileShader(gl, gl.VERTEX_SHADER, VERTEX_SRC);
  const fragment = compileShader(gl, gl.FRAGMENT_SHADER, FRAGMENT_SRC);
  if (!vertex || !fragment) return null;
  const program = gl.createProgram();
  if (!program) return null;
  gl.attachShader(program, vertex);
  gl.attachShader(program, fragment);
  gl.linkProgram(program);
  gl.deleteShader(vertex);
  gl.deleteShader(fragment);
  if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
    gl.deleteProgram(program);
    return null;
  }
  return program;
}

function openWebGLContext(canvas: HTMLCanvasElement): WebGLRenderingContext | null {
  try {
    return (canvas.getContext("webgl") ||
      canvas.getContext("experimental-webgl")) as WebGLRenderingContext | null;
  } catch {
    return null;
  }
}

// Live WebGL sketch of the Radon monogram for the /demo-pending holding
// page. Purely decorative on a public, unauthenticated leaf (see the
// "/demo-pending perimeter" tests in demo-provisioning-resilience.test.ts)
// — never the thing standing between a visitor and the page's content. A
// visitor with no WebGL support, or prefers-reduced-motion set, gets the
// mount point with nothing in it: the plain static card, unchanged.
export default function DemoPendingLogo() {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const { theme } = useTheme();

  useEffect(() => {
    const container = containerRef.current;
    if (!container || prefersReducedMotion()) return;

    const canvas = document.createElement("canvas");
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = SIZE * dpr;
    canvas.height = SIZE * dpr;
    canvas.style.width = `${SIZE}px`;
    canvas.style.height = `${SIZE}px`;

    const gl = openWebGLContext(canvas);
    if (!gl) return;

    const program = createProgram(gl);
    if (!program) return;

    const buffer = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW);

    const positionLoc = gl.getAttribLocation(program, "aPosition");
    gl.enableVertexAttribArray(positionLoc);
    gl.vertexAttribPointer(positionLoc, 2, gl.FLOAT, false, 0, 0);

    const resolutionLoc = gl.getUniformLocation(program, "uResolution");
    const timeLoc = gl.getUniformLocation(program, "uTime");
    const coreLoc = gl.getUniformLocation(program, "uColorCore");
    const accentLoc = gl.getUniformLocation(program, "uColorAccent");
    const core = readThemeColor("--signal-core", "#05AD98");
    const accent = readThemeColor("--extreme", "#9e80f6");

    gl.useProgram(program);
    gl.viewport(0, 0, canvas.width, canvas.height);
    gl.uniform2f(resolutionLoc, canvas.width, canvas.height);
    gl.uniform3f(coreLoc, core[0], core[1], core[2]);
    gl.uniform3f(accentLoc, accent[0], accent[1], accent[2]);
    gl.clearColor(0, 0, 0, 0);
    gl.enable(gl.BLEND);
    gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);

    container.appendChild(canvas);

    let raf = 0;
    const start = performance.now();
    const frame = (now: number) => {
      gl.uniform1f(timeLoc, (now - start) / 1000);
      gl.clear(gl.COLOR_BUFFER_BIT);
      gl.drawArrays(gl.TRIANGLES, 0, 3);
      raf = requestAnimationFrame(frame);
    };
    raf = requestAnimationFrame(frame);

    return () => {
      cancelAnimationFrame(raf);
      gl.deleteProgram(program);
      gl.deleteBuffer(buffer);
      if (canvas.parentNode === container) container.removeChild(canvas);
    };
  }, [theme]);

  return <div ref={containerRef} className="demo-pending-logo" aria-hidden />;
}
