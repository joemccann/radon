"use client";

import { useState, useEffect } from "react";
import { Moon, Sun } from "lucide-react";
import {
  SignalSummary,
  PortfolioConvexity,
  CircularScan,
  EnergyDistribution,
  SemanticStates,
  DenseNumericTable,
} from "@/components/kit";

export default function KitPage() {
  const [theme, setTheme] = useState<"dark" | "light">("dark");

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
  }, [theme]);

  return (
    <div
      style={{
        background: "var(--bg-base)",
        minHeight: "100vh",
        padding: 32,
        transition: "background 150ms ease-in-out",
      }}
    >
      <div className="flex justify-between items-center" style={{ marginBottom: 32 }}>
        <p
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: 11,
            letterSpacing: "0.1em",
            textTransform: "uppercase",
            color: "var(--text-muted)",
          }}
        >
          Radon Contributor Kit / Component Spec
        </p>
        <button
          onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
          style={{
            width: 32,
            height: 32,
            background: "transparent",
            border: "1px solid var(--border-dim)",
            borderRadius: 4,
            color: "var(--text-secondary)",
            cursor: "pointer",
            display: "inline-flex",
            alignItems: "center",
            justifyContent: "center",
            transition: "all 150ms ease-in-out",
          }}
        >
          {theme === "dark" ? <Sun size={14} /> : <Moon size={14} />}
        </button>
      </div>

      <div
        className="grid grid-cols-1 lg:grid-cols-3"
        style={{ gap: 16, marginBottom: 16 }}
      >
        <SignalSummary />
        <PortfolioConvexity />
        <CircularScan status="Scanning" />
      </div>

      <div
        className="grid grid-cols-1 lg:grid-cols-2"
        style={{ gap: 16, marginBottom: 16 }}
      >
        <EnergyDistribution />
        <SemanticStates />
      </div>

      <DenseNumericTable />
    </div>
  );
}
