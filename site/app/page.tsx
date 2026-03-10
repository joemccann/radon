"use client";

import React, { useEffect, useState, useMemo } from "react";
import { motion, Variants, AnimatePresence, LayoutGroup } from "framer-motion";
import { Shield, Zap, Activity, ChevronRight, Github } from "lucide-react";

// --- Components for Internal Animations ---

const FlashingValue = ({ value, prefix = "", suffix = "" }: { value: string | number, prefix?: string, suffix?: string }) => {
  const [prevValue, setPrevValue] = useState(value);
  const [flash, setFlash] = useState<"up" | "down" | null>(null);

  useEffect(() => {
    if (value > prevValue) setFlash("up");
    else if (value < prevValue) setFlash("down");
    
    const timer = setTimeout(() => setFlash(null), 1000);
    setPrevValue(value);
    return () => clearTimeout(timer);
  }, [value]);

  return (
    <motion.span
      animate={flash === "up" ? { color: "#05AD98", backgroundColor: "rgba(5, 173, 152, 0.1)" } : flash === "down" ? { color: "#E85D6C", backgroundColor: "rgba(232, 93, 108, 0.1)" } : { color: "inherit", backgroundColor: "transparent" }}
      className="px-1 rounded transition-colors duration-300"
    >
      {prefix}{value}{suffix}
    </motion.span>
  );
};

const Sparkline = ({ data, color = "var(--color-accent)" }: { data: number[], color?: string }) => {
  return (
    <div className="flex items-end gap-1 h-6">
      {data.map((h, i) => (
        <motion.div
          key={i}
          initial={{ height: 0 }}
          animate={{ height: `${h * 10}%` }}
          transition={{ duration: 0.5, delay: i * 0.05 }}
          className="w-1.5 opacity-60"
          style={{ backgroundColor: color }}
        />
      ))}
    </div>
  );
};

// --- Landing Page Component ---

export default function LandingPage() {
  const [activeView, setActiveView] = useState<"REGIME" | "PORTFOLIO" | "FLOW">("REGIME");
  const [isAutoPlaying, setIsAutoPlaying] = useState(true);
  const [tick, setTick] = useState(0);

  // Simulate real-time data ticks
  useEffect(() => {
    const interval = setInterval(() => {
      setTick(t => t + 1);
    }, 3000);
    return () => clearInterval(interval);
  }, []);

  useEffect(() => {
    if (!isAutoPlaying) return;
    const interval = setInterval(() => {
      setActiveView((prev) => {
        if (prev === "REGIME") return "PORTFOLIO";
        if (prev === "PORTFOLIO") return "FLOW";
        return "REGIME";
      });
    }, 10000); // Slower cycle to appreciate the dense data
    return () => clearInterval(interval);
  }, [isAutoPlaying]);

  const handleManualSwitch = (view: "REGIME" | "PORTFOLIO" | "FLOW") => {
    setActiveView(view);
    setIsAutoPlaying(false);
  };

  const containerVariants: Variants = {
    hidden: { opacity: 0 },
    visible: {
      opacity: 1,
      transition: { staggerChildren: 0.1, delayChildren: 0.2 },
    },
  };

  const itemVariants: Variants = {
    hidden: { opacity: 0, y: 20 },
    visible: {
      opacity: 1,
      y: 0,
      transition: { duration: 0.5, ease: "easeOut" },
    },
  };

  // Simulated Data Generation
  const regimeData = useMemo(() => ({
    score: 14 + (tick % 3),
    metrics: [
      { label: "VIX", value: (22.77 + (Math.random() * 0.2 - 0.1)).toFixed(2), change: "-3.4%", color: "text-primary" },
      { label: "VVIX", value: (116.75 + (Math.random() * 0.5 - 0.25)).toFixed(2), change: "+1.2%", color: "text-warn" },
      { label: "SPY", value: (682.28 + (Math.random() * 0.1 - 0.05)).toFixed(2), change: "+0.01%", color: "text-signal-strong" },
      { label: "REALIZED VOL", value: "11.80%", change: "20d", color: "text-signal-strong" },
      { label: "SECTOR CORR", value: "0.0231", change: "Intraday", color: "text-primary" }
    ]
  }), [tick]);

  const portfolioData = useMemo(() => ({
    defined: [
      { t: "AAOI", s: "Long Call $105", q: "50", u: (123.36 + (Math.random() * 0.4 - 0.2)).toFixed(2), l: (22.20 + (Math.random() * 0.1 - 0.05)).toFixed(2), d: "+64.08%", p: "+43,350", e: "2026-03-20" },
      { t: "AAPL", s: "Bull Call Spread", q: "100", u: (262.15 + (Math.random() * 0.2 - 0.1)).toFixed(2), l: (4.65 + (Math.random() * 0.05 - 0.02)).toFixed(2), d: "+11.24%", p: "+4,700", e: "2026-04-17" },
      { t: "AMD", s: "Long Call $195", q: "20", u: (205.65 + (Math.random() * 0.5 - 0.25)).toFixed(2), l: (49.85 + (Math.random() * 0.2 - 0.1)).toFixed(2), d: "+4.22%", p: "+4,040", e: "2027-01-15" },
      { t: "BAP", s: "Long Call $380", q: "1", u: (344.12 + (Math.random() * 0.5)).toFixed(2), l: "3.04", d: "+0.00%", p: "+$0", e: "2026-05-15" },
      { t: "BRZE", s: "Long Call $22.5", q: "120", u: (19.01 + (Math.random() * 0.1)).toFixed(2), l: "0.18", d: "-59.30%", p: "-11,500", e: "2026-03-20" },
    ],
    undefined: [
      { t: "APO", s: "Risk Reversal", q: "25", u: (108.13 + (Math.random() * 0.3)).toFixed(2), l: "0.55", d: "+450.0%", p: "+2,335", e: "2026-04-17" },
      { t: "IGV", s: "Synthetic Long", q: "40", u: (85.60 + (Math.random() * 0.2)).toFixed(2), l: "-3.60", d: "-122.2%", p: "+5,144", e: "2026-05-15" },
      { t: "IWM", s: "Risk Reversal", q: "6", u: (254.50 + (Math.random() * 0.4)).toFixed(2), l: "1.06", d: "+325.5%", p: "+1,840", e: "2026-04-17" },
      { t: "ETHA", s: "Long Call $15", q: "200", u: (15.69 + (Math.random() * 0.1)).toFixed(2), l: "2.75", d: "+6.18%", p: "+3,200", e: "2026-06-18" },
      { t: "GOOG", s: "Bull Call Spread", q: "44", u: (308.18 + (Math.random() * 0.5)).toFixed(2), l: "7.29", d: "+5.81%", p: "+4,504", e: "2026-04-17" },
    ],
    equity: [
      { t: "EC", s: "Stock (5000.0)", q: "5000", u: "12.37", l: (12.83 + (Math.random() * 0.05)).toFixed(2), d: "-0.08%", p: "+2,288", e: "---" },
      { t: "MSFT", s: "Stock (1000.0)", q: "1000", u: "468.51", l: (404.44 + (Math.random() * 0.2)).toFixed(2), d: "-1.21%", p: "-64,065", e: "---" },
      { t: "NAK", s: "Stock (18628.0)", q: "18628", u: "3.04", l: (1.40 + (Math.random() * 0.01)).toFixed(2), d: "+3.82%", p: "-30,563", e: "---" },
      { t: "PLTR", s: "Stock (2500.0)", q: "2500", u: "111.10", l: (112.45 + (Math.random() * 0.1)).toFixed(2), d: "+1.2%", p: "+3,400", e: "---" },
      { t: "TSLA", s: "Stock (500.0)", q: "500", u: "254.12", l: (252.80 + (Math.random() * 0.5)).toFixed(2), d: "-0.5%", p: "-650", e: "---" },
      { t: "NVDA", s: "Stock (1200.0)", q: "1200", u: "135.45", l: (138.20 + (Math.random() * 0.3)).toFixed(2), d: "+2.1%", p: "+3,300", e: "---" },
    ]
  }), [tick]);

  const flowData = useMemo(() => [
    { t: "TMUS", p: "Bull Call Spread", f: "83% ACCUM", s: [8, 9, 9, 7, 8, 9, 6], sig: "Strong institutional accumulation" },
    { t: "GOOG", p: "Bull Call Spread", f: "80% ACCUM", s: [7, 8, 8, 9, 9, 8, 7], sig: "Strong institutional accumulation" },
    { t: "IGV", p: "Synthetic Long", f: "77% ACCUM", s: [8, 6, 4, 7, 9, 8, 7], sig: "Moderate accumulation signal" },
    { t: "IWM", p: "Risk Reversal", f: "74% ACCUM", s: [6, 7, 8, 8, 7, 6, 5], sig: "Moderate accumulation signal" },
    { t: "BRZE", p: "Long Call", f: "73% DISTRIB", s: [4, 3, 2, 5, 4, 3, 2], sig: "Weak distribution signal", type: 'distrib' },
    { t: "BRZE", p: "Bull Call Spread", f: "73% DISTRIB", s: [3, 4, 2, 4, 3, 4, 2], sig: "Weak distribution signal", type: 'distrib' },
    { t: "AAPL", p: "Bull Call Spread", f: "66% ACCUM", s: [5, 6, 7, 5, 4, 5, 6], sig: "Weak accumulation signal" },
    { t: "RR", p: "Stock (10000.0)", f: "64% ACCUM", s: [4, 5, 6, 5, 4, 5, 4], sig: "Weak accumulation signal" },
    { t: "PLTR", p: "Long Call", f: "63% ACCUM", s: [3, 4, 5, 4, 3, 4, 3], sig: "Weak accumulation signal" },
    { t: "BAP", p: "Long Call", f: "61% ACCUM", s: [2, 3, 4, 3, 2, 3, 2], sig: "Weak accumulation signal" },
    { t: "META", p: "Iron Condor", f: "58% DISTRIB", s: [5, 4, 3, 4, 5, 4, 3], sig: "Neutral distribution", type: 'distrib' },
    { t: "NVDA", p: "Long Put $120", f: "55% ACCUM", s: [6, 5, 4, 5, 6, 5, 4], sig: "Emerging accumulation" },
  ], [tick]);

  const renderMockView = () => {
    switch (activeView) {
      case "REGIME":
        return (
          <motion.div key="regime" initial={{ opacity: 0, x: 20 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: -20 }} className="flex flex-col gap-6">
            <div className="border border-grid p-6 bg-panel/50 relative overflow-hidden">
              <motion.div animate={{ x: ["0%", "100%"] }} transition={{ duration: 4, repeat: Infinity, ease: "linear" }} className="absolute top-0 left-0 w-1/2 h-full bg-gradient-to-r from-transparent via-accent/5 to-transparent pointer-events-none" />
              <div className="flex justify-between items-start mb-2 relative z-10">
                 <div>
                    <div className="text-[9px] font-mono text-muted uppercase tracking-[0.2em] mb-1">Structural Regime Score</div>
                    <div className="flex items-baseline gap-2">
                       <span className="text-6xl font-mono font-bold text-accent"><FlashingValue value={regimeData.score} /></span>
                       <span className="text-2xl font-mono text-muted">/100</span>
                    </div>
                 </div>
                 <div className="text-right font-mono">
                    <div className="text-[9px] text-muted uppercase mb-1">Last Scan</div>
                    <div className="text-[10px] text-primary">10:51:{11 + (tick % 60)} AM</div>
                 </div>
              </div>
              <div className="flex gap-1 h-2 w-full bg-grid mt-4 relative z-10">
                 <motion.div animate={{ width: `${regimeData.score}%` }} className="bg-accent h-full" />
              </div>
            </div>

            <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
              {regimeData.metrics.map((m, i) => (
                <div key={i} className="border border-grid p-3 bg-panel-raised/30">
                  <div className="text-[8px] font-mono text-muted uppercase tracking-widest mb-1">{m.label}</div>
                  <div className={`text-lg font-mono font-medium ${m.color}`}><FlashingValue value={m.value} /></div>
                  <div className="text-[9px] font-mono text-muted mt-1">{m.change}</div>
                </div>
              ))}
            </div>

            <div className="flex-1 border border-grid bg-panel-raised/10 flex flex-col min-h-0">
               <div className="flex items-center justify-between px-4 py-2 border-b border-grid bg-panel-raised/20">
                  <div className="text-[9px] font-mono uppercase tracking-widest text-muted">Active Flow Events</div>
                  <div className="w-2 h-2 bg-signal-strong rounded-full animate-pulse shadow-[0_0_8px_var(--color-signal-strong)]" />
               </div>
               <div className="flex-1 overflow-hidden font-mono text-[10px]">
                  <table className="w-full text-left border-collapse">
                    <tbody>
                      {[
                        { ticker: "AAOI", structure: "Long Call $105", price: (123.36 + (Math.random() * 0.1)).toFixed(2), pnl: "+65,515", type: 'accum' },
                        { ticker: "AAPL", structure: "Bull Call Spread", price: (262.15 + (Math.random() * 0.1)).toFixed(2), pnl: "+6,396", type: 'accum' },
                        { ticker: "BRZE", structure: "Long Call $22.5", price: (19.01 + (Math.random() * 0.1)).toFixed(2), pnl: "-11,500", type: 'distrib' },
                        { ticker: "PLTR", structure: "Long Call $45", price: (111.10 + (Math.random() * 0.1)).toFixed(2), pnl: "+32,565", type: 'accum' },
                        { ticker: "TSLA", structure: "Iron Condor", price: (254.12 + (Math.random() * 0.1)).toFixed(2), pnl: "-4,210", type: 'distrib' },
                        { ticker: "NVDA", structure: "Long Put $120", price: (135.45 + (Math.random() * 0.1)).toFixed(2), pnl: "+12,840", type: 'accum' },
                        { ticker: "META", structure: "Bull Call Spread", price: (482.90 + (Math.random() * 0.1)).toFixed(2), pnl: "-8,450", type: 'distrib' },
                      ].map((row, i) => (
                        <tr key={i} className="border-b border-grid/50">
                          <td className="p-3 font-bold text-accent">{row.ticker}</td>
                          <td className="p-3 text-muted">{row.structure}</td>
                          <td className="p-3"><FlashingValue value={row.price} /></td>
                          <td className={`p-3 text-right font-bold ${row.type === 'accum' ? 'text-signal-strong' : 'text-negative'}`}>{row.pnl}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
               </div>
            </div>
          </motion.div>
        );
      case "FLOW":
        return (
          <motion.div key="flow" initial={{ opacity: 0, x: 20 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: -20 }} className="flex flex-col gap-4 overflow-y-auto max-h-full pr-2 scrollbar-thin scrollbar-thumb-grid">
            <div className="flex items-center justify-between">
              <div className="text-[10px] font-mono text-primary flex items-center gap-2 uppercase tracking-widest">
                <Shield size={12} className="text-accent" /> Flow Analysis Matrix
              </div>
              <div className="text-[8px] font-mono text-muted uppercase tracking-widest">19 POSITIONS</div>
            </div>
            <div className="border border-grid bg-panel/30">
              <table className="w-full text-left font-mono text-[9px] border-collapse">
                <thead>
                  <tr className="border-b border-grid text-muted bg-panel-raised/50">
                    <th className="p-3 font-medium uppercase tracking-widest text-[8px]">Ticker</th>
                    <th className="p-3 font-medium uppercase tracking-widest text-[8px]">Position</th>
                    <th className="p-3 font-medium uppercase tracking-widest text-[8px]">Flow</th>
                    <th className="p-3 font-medium uppercase tracking-widest text-[8px]">Strength</th>
                    <th className="p-3 font-medium uppercase tracking-widest text-[8px]">Signal Note</th>
                  </tr>
                </thead>
                <tbody>
                  {flowData.map((row, i) => (
                    <tr key={i} className="border-b border-grid/30 hover:bg-panel-raised/40 transition-colors">
                      <td className="p-3 font-bold text-primary">{row.t}</td>
                      <td className="p-3 text-muted">{row.p}</td>
                      <td className="p-3">
                        <span className={`px-2 py-0.5 border ${row.type === 'distrib' ? 'border-negative/30 text-negative' : 'border-accent/30 text-accent'} text-[8px] font-bold`}>{row.f}</span>
                      </td>
                      <td className="p-3"><Sparkline data={row.s} color={row.type === 'distrib' ? 'var(--color-negative)' : 'var(--color-accent)'} /></td>
                      <td className="p-3 text-muted italic">{row.sig}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </motion.div>
        );
      case "PORTFOLIO":
        return (
          <motion.div key="portfolio" initial={{ opacity: 0, x: 20 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: -20 }} className="flex flex-col gap-6 overflow-y-auto max-h-full pr-2 scrollbar-thin scrollbar-thumb-grid">
            {/* Defined Risk Section */}
            <div className="space-y-2">
              <div className="flex items-center justify-between">
                <div className="text-[10px] font-mono text-primary flex items-center gap-2 uppercase tracking-widest">
                  <Shield size={12} className="text-accent" /> Defined Risk Positions
                </div>
                <div className="text-[8px] font-mono text-muted uppercase tracking-widest">5 POSITIONS</div>
              </div>
              <div className="border border-grid bg-panel/30">
                <table className="w-full text-left font-mono text-[9px] border-collapse">
                  <tbody className="divide-y divide-grid/30">
                    {portfolioData.defined.map((row, i) => (
                      <tr key={i} className="hover:bg-panel-raised/40 transition-colors">
                        <td className="p-3 font-bold text-accent">{row.t}</td>
                        <td className="p-3 text-muted">{row.s}</td>
                        <td className="p-3"><FlashingValue value={row.u} /></td>
                        <td className="p-3 font-bold"><FlashingValue value={row.l} /></td>
                        <td className="p-3 text-right text-signal-strong font-bold">{row.p}</td>
                        <td className="p-3 text-right text-muted">{row.e}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>

            {/* Undefined Risk Section */}
            <div className="space-y-2">
              <div className="flex items-center justify-between">
                <div className="text-[10px] font-mono text-warn flex items-center gap-2 uppercase tracking-widest">
                  <Zap size={12} /> Undefined Risk Positions
                </div>
                <div className="text-[8px] font-mono text-muted uppercase tracking-widest">5 POSITIONS</div>
              </div>
              <div className="border border-grid bg-panel/30">
                <table className="w-full text-left font-mono text-[9px] border-collapse">
                  <tbody className="divide-y divide-grid/30">
                    {portfolioData.undefined.map((row, i) => (
                      <tr key={i} className="hover:bg-panel-raised/40 transition-colors">
                        <td className="p-3 font-bold text-warn">{row.t}</td>
                        <td className="p-3 text-muted">{row.s}</td>
                        <td className="p-3"><FlashingValue value={row.u} /></td>
                        <td className="p-3 font-bold"><FlashingValue value={row.l} /></td>
                        <td className="p-3 text-right text-signal-strong font-bold">{row.p}</td>
                        <td className="p-3 text-right text-muted">{row.e}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>

            {/* Equity Section */}
            <div className="space-y-2">
              <div className="flex items-center justify-between">
                <div className="text-[10px] font-mono text-primary flex items-center gap-2 uppercase tracking-widest">
                  <Activity size={12} className="text-accent" /> Equity Positions
                </div>
                <div className="text-[8px] font-mono text-muted uppercase tracking-widest">6 POSITIONS</div>
              </div>
              <div className="border border-grid bg-panel/30">
                <table className="w-full text-left font-mono text-[9px] border-collapse">
                  <tbody className="divide-y divide-grid/30">
                    {portfolioData.equity.map((row, i) => (
                      <tr key={i} className="hover:bg-panel-raised/40 transition-colors">
                        <td className="p-3 font-bold text-accent">{row.t}</td>
                        <td className="p-3 text-muted">{row.s}</td>
                        <td className="p-3 text-muted">{row.u}</td>
                        <td className="p-3 font-bold"><FlashingValue value={row.l} /></td>
                        <td className={`p-3 text-right font-bold ${row.p.startsWith('+') ? 'text-signal-strong' : 'text-negative'}`}>{row.p}</td>
                        <td className="p-3 text-right text-muted">{row.e}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </motion.div>
        );
    }
  };

  return (
    <div className="min-h-screen bg-canvas selection:bg-accent selection:text-canvas overflow-x-hidden">
      <div className="fixed inset-0 instrument-grid opacity-[0.03] pointer-events-none" />

      {/* Header */}
      <header className="fixed top-0 left-0 right-0 z-50 border-b border-grid bg-canvas/80 backdrop-blur-md">
        <div className="max-w-7xl mx-auto px-6 h-16 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <img src="/brand/radon-monogram.svg" alt="Radon" className="w-5 h-5" />
            <span className="font-display font-bold text-xl tracking-tight uppercase">Radon</span>
          </div>
          <nav className="hidden md:flex items-center gap-8">
            <a href="#features" className="text-xs font-mono text-muted hover:text-primary transition-colors uppercase tracking-widest">Instruments</a>
            <a href="#open-source" className="text-xs font-mono text-muted hover:text-primary transition-colors uppercase tracking-widest">Sovereignty</a>
            <a href="https://github.com/radon-terminal" className="text-xs font-mono text-muted hover:text-primary transition-colors uppercase tracking-widest">Source</a>
          </nav>
          <button className="px-4 py-2 bg-primary text-canvas text-xs font-mono font-bold uppercase tracking-widest hover:bg-accent transition-all">
            Access Terminal
          </button>
        </div>
      </header>

      <main className="pt-32 pb-20">
        {/* Hero Section */}
        <section className="max-w-7xl mx-auto px-6 mb-32">
          <motion.div initial="hidden" animate="visible" variants={containerVariants} className="flex flex-col items-center text-center">
            <motion.h1 variants={itemVariants} className="text-5xl md:text-7xl lg:text-8xl font-display font-bold mb-8 max-w-5xl leading-[1.05] tracking-tight">
              Market structure, <span className="text-muted">reconstructed.</span>
            </motion.h1>
            <motion.p variants={itemVariants} className="text-lg md:text-xl text-secondary max-w-2xl mb-12 font-sans leading-relaxed text-center">
              Radon Terminal is an institutional-grade instrument for decomposing flow and volatility. Built for the 1% of technical traders who demand sovereignty over their strategy.
            </motion.p>
            <motion.div variants={itemVariants} className="flex flex-col md:flex-row gap-4">
              <button className="px-8 py-4 bg-accent text-canvas font-mono font-bold uppercase tracking-widest hover:brightness-110 transition-all flex items-center gap-2">
                Initialize Connection <ChevronRight size={16} />
              </button>
              <button className="px-8 py-4 border border-grid text-primary font-mono font-bold uppercase tracking-widest hover:bg-panel-raised transition-all">
                View Documentation
              </button>
            </motion.div>
          </motion.div>
        </section>

        {/* Terminal Preview */}
        <section className="max-w-7xl mx-auto px-6 mb-40">
          <motion.div initial={{ opacity: 0, scale: 0.95 }} whileInView={{ opacity: 1, scale: 1 }} viewport={{ once: true }} transition={{ duration: 1, ease: "easeOut" }} className="relative">
            <div className="border border-grid bg-panel rounded-lg overflow-hidden shadow-2xl">
              <LayoutGroup>
                <div className="h-10 border-b border-grid bg-panel-raised flex items-center justify-between px-4">
                  <div className="flex gap-2">
                    <div className="w-2.5 h-2.5 rounded-full bg-grid" />
                    <div className="w-2.5 h-2.5 rounded-full bg-grid" />
                    <div className="w-2.5 h-2.5 rounded-full bg-grid" />
                  </div>
                  <div className="text-[10px] font-mono text-muted uppercase tracking-widest">radon-terminal // telemetry-link: connected</div>
                  <div className="flex items-center gap-4">
                     <div className="text-[10px] font-mono text-signal-strong uppercase tracking-widest flex items-center gap-2">
                        <div className="w-1.5 h-1.5 bg-signal-strong rounded-full animate-pulse shadow-[0_0_8px_var(--color-signal-strong)]" /> SYNC: OK
                     </div>
                  </div>
                </div>
                <div className="p-1 flex flex-col md:flex-row gap-1 h-[650px]">
                  <div className="w-full md:w-48 border-r border-grid p-4 hidden md:flex flex-col gap-6 bg-canvas/40 backdrop-blur-sm">
                    <div className="space-y-6">
                      <div className="space-y-1">
                        <div className="text-[9px] font-mono text-muted uppercase tracking-widest mb-3 px-2">Instruments</div>
                        <button onClick={() => handleManualSwitch("REGIME")} className={`h-8 w-full flex items-center px-2 text-[10px] font-mono transition-all border-l-2 relative group ${activeView === "REGIME" ? 'bg-accent/10 border-accent text-primary' : 'border-transparent text-muted hover:bg-panel-raised hover:text-secondary'}`}>
                          REGIME {activeView === "REGIME" && <motion.div layoutId="nav-active" className="absolute left-0 w-full h-full bg-accent/5 pointer-events-none" />}
                        </button>
                        <button onClick={() => handleManualSwitch("PORTFOLIO")} className={`h-8 w-full flex items-center px-2 text-[10px] font-mono transition-all border-l-2 relative group ${activeView === "PORTFOLIO" ? 'bg-accent/10 border-accent text-primary' : 'border-transparent text-muted hover:bg-panel-raised hover:text-secondary'}`}>
                          PORTFOLIO {activeView === "PORTFOLIO" && <motion.div layoutId="nav-active" className="absolute left-0 w-full h-full bg-accent/5 pointer-events-none" />}
                        </button>
                        <button onClick={() => handleManualSwitch("FLOW")} className={`h-8 w-full flex items-center px-2 text-[10px] font-mono transition-all border-l-2 relative group ${activeView === "FLOW" ? 'bg-accent/10 border-accent text-primary' : 'border-transparent text-muted hover:bg-panel-raised hover:text-secondary'}`}>
                          FLOW {activeView === "FLOW" && <motion.div layoutId="nav-active" className="absolute left-0 w-full h-full bg-accent/5 pointer-events-none" />}
                        </button>
                      </div>
                    </div>
                    <div className="mt-auto border-t border-grid pt-4 space-y-4 px-2">
                      <div className="flex justify-between items-center text-[8px] font-mono text-muted uppercase tracking-widest">
                        <span>IB Gateway</span>
                        <div className="w-1.5 h-1.5 bg-signal-strong rounded-full" />
                      </div>
                      <div className="h-0.5 w-full bg-grid/30 relative overflow-hidden">
                        <motion.div animate={{ x: ["-100%", "100%"] }} transition={{ duration: 3, repeat: Infinity, ease: "linear" }} className="absolute inset-y-0 w-1/2 bg-accent/20" />
                      </div>
                    </div>
                  </div>
                  <div className="flex-1 p-6 overflow-hidden bg-canvas/30 relative">
                    <AnimatePresence mode="wait">{renderMockView()}</AnimatePresence>
                  </div>
                </div>
              </LayoutGroup>
            </div>
            <div className="absolute -top-10 -right-10 w-40 h-40 border border-accent/20 rounded-full opacity-20 animate-ping" />
            <div className="absolute -bottom-20 -left-20 w-80 h-80 border border-accent/10 rounded-full opacity-10" />
          </motion.div>
        </section>

        {/* Features Section */}
        <section id="features" className="max-w-7xl mx-auto px-6 mb-40">
          <div className="grid md:grid-cols-3 gap-12">
            {[
              { icon: Activity, title: "Radon Flow", text: "Decompose institutional flow into principal components. Isolate non-random signals from the noise of retail positioning." },
              { icon: Zap, title: "Radon Surface", text: "Map volatility surfaces in real-time. Detect structural dislocations and convexity traps before they materialize." },
              { icon: Shield, title: "Radon Structure", text: "Reconstruct cross-asset state from fragmented data. A scientific approach to regime detection and transition probability." }
            ].map((f, i) => (
              <motion.div key={i} initial={{ opacity: 0, y: 20 }} whileInView={{ opacity: 1, y: 0 }} viewport={{ once: true }} transition={{ delay: i * 0.1 }} className="border-l-2 border-grid pl-8 py-4 hover:border-accent transition-colors group">
                <f.icon className="text-accent mb-6 group-hover:scale-110 transition-transform" size={32} />
                <h3 className="text-xl font-display font-bold mb-4 uppercase tracking-tight">{f.title}</h3>
                <p className="text-secondary font-sans leading-relaxed">{f.text}</p>
              </motion.div>
            ))}
          </div>
        </section>

        {/* Sovereignty Section */}
        <section id="open-source" className="max-w-7xl mx-auto px-6 mb-40">
          <motion.div initial={{ opacity: 0 }} whileInView={{ opacity: 1 }} viewport={{ once: true }} className="bg-panel border border-grid p-12 md:p-24 relative overflow-hidden">
            <div className="relative z-10 max-w-3xl">
              <h2 className="text-4xl md:text-5xl font-display font-bold mb-8 leading-tight">
                Open Source because we aren't afraid of a code audit. <span className="text-accent underline decoration-accent/30 underline-offset-8">Are you?</span>
              </h2>
              <p className="text-xl text-secondary mb-12 font-sans leading-relaxed">
                We don't hide behind black boxes. The math is public. The execution is transparent. If you can't verify your tools, you don't own your strategy.
              </p>
              <a href="https://github.com" className="inline-flex items-center gap-2 font-mono text-sm font-bold uppercase tracking-widest text-primary hover:text-accent transition-colors">
                <Github size={20} /> Inspect Source
              </a>
            </div>
            <div className="absolute top-0 right-0 w-1/3 h-full opacity-5 pointer-events-none overflow-hidden">
               <div className="absolute inset-0 rotate-45 translate-x-1/2 scale-150">
                  {[...Array(20)].map((_, i) => (
                    <div key={i} className="h-px w-full bg-accent mb-4" />
                  ))}
               </div>
            </div>
          </motion.div>
        </section>

        <footer className="max-w-7xl mx-auto px-6 pt-20 border-t border-grid">
          <div className="grid grid-cols-2 md:grid-cols-4 gap-12 mb-20">
            <div>
              <div className="flex items-center gap-2 mb-6">
                <img src="/brand/radon-monogram.svg" alt="Radon" className="w-4 h-4" />
                <span className="font-display font-bold text-sm tracking-tight uppercase">Radon</span>
              </div>
              <p className="text-xs font-mono text-muted leading-loose uppercase">Reconstructing market structure from noisy signals.</p>
            </div>
            <div>
              <h4 className="text-[10px] font-mono font-bold text-primary uppercase tracking-[0.2em] mb-6">Instruments</h4>
              <ul className="space-y-3 text-xs font-mono text-muted">
                <li><a href="#" className="hover:text-accent transition-colors">FLOW</a></li>
                <li><a href="#" className="hover:text-accent transition-colors">SURFACE</a></li>
                <li><a href="#" className="hover:text-accent transition-colors">SIGNALS</a></li>
              </ul>
            </div>
            <div>
              <h4 className="text-[10px] font-mono font-bold text-primary uppercase tracking-[0.2em] mb-6">Protocol</h4>
              <ul className="space-y-3 text-xs font-mono text-muted">
                <li><a href="#" className="hover:text-accent transition-colors">DOCUMENTATION</a></li>
                <li><a href="#" className="hover:text-accent transition-colors">API SPEC</a></li>
                <li><a href="#" className="hover:text-accent transition-colors">CONTRIBUTE</a></li>
              </ul>
            </div>
            <button className="h-fit py-3 border border-accent/50 text-accent text-[10px] font-mono font-bold uppercase tracking-[0.2em] hover:bg-accent hover:text-canvas transition-all">
              Access Live Node
            </button>
          </div>
          <div className="pb-10 flex flex-col md:flex-row justify-between items-center gap-4 text-[10px] font-mono text-muted uppercase tracking-widest">
            <span>© 2026 RADON PROTOCOL // ALL RIGHTS RESERVED</span>
            <div className="flex gap-8 items-center">
              <span className="flex items-center gap-2"><div className="w-1.5 h-1.5 bg-signal-strong rounded-full" /> Operational</span>
              <span>Latency: 14ms</span>
            </div>
          </div>
        </footer>
      </main>
    </div>
  );
}
