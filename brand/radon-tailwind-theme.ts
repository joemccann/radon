export const radonTheme = {
  colors: {
    bg: {
      canvas: "#0a0f14",
      panel: "#0f1519",
      panelRaised: "#151c22",
    },
    line: {
      grid: "#1e293b",
    },
    text: {
      primary: "#e2e8f0",
      secondary: "#94a3b8",
      muted: "#475569",
    },
    signal: {
      core: "#05AD98",
      strong: "#0FCFB5",
      deep: "#048A7A",
    },
    semantic: {
      warn: "#F5A623",
      fault: "#E85D6C",
      dislocation: "#D946A8",
      extreme: "#8B5CF6",
      neutral: "#94a3b8",
    },
  },
  borderRadius: {
    panel: "4px",
    badge: "999px",
    focus: "6px",
  },
  spacing: {
    1: "4px",
    2: "8px",
    3: "12px",
    4: "16px",
    6: "24px",
    8: "32px",
  },
  fontFamily: {
    sans: ["Inter", "ui-sans-serif", "system-ui", "sans-serif"],
    mono: ["IBM Plex Mono", "ui-monospace", "SFMono-Regular", "monospace"],
    display: ["Söhne", "Inter", "ui-sans-serif", "system-ui", "sans-serif"],
  },
  boxShadow: {
    none: "none",
  },
  letterSpacing: {
    instrument: "0.02em",
    meta: "0.03em",
  },
  fontSize: {
    meta: ["11px", { lineHeight: "1.35" }],
    body: ["12px", { lineHeight: "1.4" }],
    table: ["13px", { lineHeight: "1.35" }],
    panelTitle: ["14px", { lineHeight: "1.2" }],
    viewTitle: ["18px", { lineHeight: "1.2" }],
    metric: ["28px", { lineHeight: "1.05" }],
  },
};

export default radonTheme;
