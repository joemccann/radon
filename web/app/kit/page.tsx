"use client";

import { Moon, Sun } from "lucide-react";
import {
  SignalSummary,
  PortfolioConvexity,
  CircularScan,
  EnergyDistribution,
  SemanticStates,
  DenseNumericTable,
} from "@/components/kit";
import { MarkovStateGraph } from "@/components/instruments";
import { useTheme } from "@/lib/ThemeContext";

const MARKOV_DEMO_STATES = [
  { id: "r1", label: "REGIME 1" },
  { id: "r2", label: "REGIME 2", current: true },
  { id: "r3", label: "REGIME 3" },
  { id: "r4", label: "REGIME 4" },
];

const MARKOV_DEMO_TRANSITIONS = [
  { from: "r1", to: "r2", probability: 0.55 },
  { from: "r2", to: "r3", probability: 0.27 },
  { from: "r2", to: "r2", probability: 0.62 },
  { from: "r2", to: "r1", probability: 0.11 },
  { from: "r3", to: "r4", probability: 0.41 },
  { from: "r3", to: "r2", probability: 0.18 },
  { from: "r4", to: "r1", probability: 0.9 },
  { from: "r1", to: "r3", probability: 0.22 },
];

export default function KitPage() {
  const { theme, toggleTheme } = useTheme();

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
          onClick={toggleTheme}
          aria-label="Toggle theme"
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
            transition: "background-color 150ms cubic-bezier(0.25,1,0.5,1), border-color 150ms cubic-bezier(0.25,1,0.5,1), color 150ms cubic-bezier(0.25,1,0.5,1)",
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

      <div
        style={{
          background: "var(--bg-panel)",
          border: "1px solid var(--border-dim)",
          borderRadius: 4,
          padding: 24,
          marginTop: 16,
        }}
      >
        <div className="flex justify-between items-start" style={{ marginBottom: 16 }}>
          <div>
            <p
              style={{
                fontFamily: "var(--font-mono)",
                fontSize: 10,
                letterSpacing: "0.1em",
                textTransform: "uppercase",
                color: "var(--text-muted)",
                marginBottom: 4,
              }}
            >
              Markov Engine / Lattice
            </p>
            <h3
              style={{
                fontSize: 14,
                fontWeight: 600,
                color: "var(--text-primary)",
              }}
            >
              State Transition Graph
            </h3>
          </div>
          <span
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: 10,
              letterSpacing: "0.1em",
              textTransform: "uppercase",
              color: "var(--signal-core-text)",
            }}
          >
            Shifting
          </span>
        </div>
        <MarkovStateGraph
          states={MARKOV_DEMO_STATES}
          transitions={MARKOV_DEMO_TRANSITIONS}
          caption="60d sample"
        />
      </div>
    </div>
  );
}
