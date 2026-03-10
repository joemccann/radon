"use client";

import React from "react";
import { motion, Variants } from "framer-motion";
import { Terminal, Shield, Zap, Activity, ChevronRight, Github } from "lucide-react";

export default function LandingPage() {
  const [activeView, setActiveView] = React.useState<"REGIME" | "PORTFOLIO" | "FLOW">("REGIME");

  const containerVariants: Variants = {
    hidden: { opacity: 0 },
    visible: {
      opacity: 1,
      transition: {
        staggerChildren: 0.1,
        delayChildren: 0.2,
      },
    },
  };

  const itemVariants: Variants = {
    hidden: { opacity: 0, y: 20 },
    visible: {
      opacity: 1,
      y: 0,
      transition: {
        duration: 0.5,
        ease: "easeOut",
      },
    },
  };

  const lineVariants: Variants = {
    hidden: { scaleX: 0 },
    visible: {
      scaleX: 1,
      transition: { duration: 1, ease: "easeInOut" },
    },
  };

  const renderMockView = () => {
    switch (activeView) {
      case "REGIME":
        return (
          <motion.div 
            key="regime"
            initial={{ opacity: 0, x: 20 }}
            animate={{ opacity: 1, x: 0 }}
            exit={{ opacity: 0, x: -20 }}
            className="flex flex-col gap-6"
          >
            {/* Regime Score Area */}
            <div className="border border-grid p-6 bg-panel/50 relative">
              <div className="flex justify-between items-start mb-2">
                 <div>
                    <div className="text-[9px] font-mono text-muted uppercase tracking-[0.2em] mb-1">Structural Regime Score</div>
                    <div className="flex items-baseline gap-2">
                       <span className="text-6xl font-mono font-bold text-accent">14</span>
                       <span className="text-2xl font-mono text-muted">/100</span>
                    </div>
                 </div>
                 <div className="text-right">
                    <div className="text-[9px] font-mono text-muted uppercase mb-1">Last Scan</div>
                    <div className="text-[10px] font-mono text-primary">10:51:11 AM</div>
                 </div>
              </div>
              <div className="flex gap-1 h-2 w-full bg-grid mt-4">
                 <div className="w-[14%] bg-accent" />
              </div>
              <div className="flex justify-between mt-2 text-[8px] font-mono text-muted uppercase tracking-widest">
                 <span>Low</span>
                 <span>Elevated</span>
                 <span>High</span>
                 <span>Critical</span>
              </div>
            </div>

            {/* Metrics Grid */}
            <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
              {[
                { label: "VIX", value: "22.77", change: "-3.4%", color: "text-primary" },
                { label: "VVIX", value: "116.75", change: "+1.2%", color: "text-warn" },
                { label: "SPY", value: "682.28", change: "+0.01%", color: "text-signal-strong" },
                { label: "REALIZED VOL", value: "11.80%", change: "20d", color: "text-signal-strong" },
                { label: "SECTOR CORR", value: "0.0231", change: "Intraday", color: "text-primary" }
              ].map((m, i) => (
                <div key={i} className="border border-grid p-3 bg-panel-raised/30">
                  <div className="text-[8px] font-mono text-muted uppercase tracking-widest mb-1">{m.label}</div>
                  <div className={`text-lg font-mono font-medium ${m.color}`}>{m.value}</div>
                  <div className="text-[9px] font-mono text-muted mt-1">{m.change}</div>
                </div>
              ))}
            </div>

            {/* Mini Table */}
            <div className="flex-1 border border-grid bg-panel-raised/10 flex flex-col min-h-0">
               <div className="flex items-center justify-between px-4 py-2 border-b border-grid bg-panel-raised/20">
                  <div className="text-[9px] font-mono uppercase tracking-widest text-muted">Active Flow Events</div>
                  <div className="w-2 h-2 bg-signal-strong rounded-full animate-pulse" />
               </div>
               <div className="flex-1 overflow-hidden font-mono text-[10px]">
                  <table className="w-full text-left border-collapse">
                    <thead>
                      <tr className="border-b border-grid text-muted">
                        <th className="p-3 font-medium uppercase tracking-widest">Ticker</th>
                        <th className="p-3 font-medium uppercase tracking-widest">Structure</th>
                        <th className="p-3 font-medium uppercase tracking-widest">Price</th>
                        <th className="p-3 font-medium uppercase tracking-widest text-right">P&L</th>
                      </tr>
                    </thead>
                    <tbody>
                      {[
                        { ticker: "AAOI", structure: "Long Call $105", price: "123.36", pnl: "+65,515", active: true, pos: true },
                        { ticker: "AAPL", structure: "Bull Call Spread", price: "262.15", pnl: "+6,396", active: false, pos: true },
                      ].map((row, i) => (
                        <tr key={i} className={`border-b border-grid/50 transition-colors ${row.active ? 'bg-signal-strong/5' : ''}`}>
                          <td className="p-3 font-bold">{row.ticker}</td>
                          <td className="p-3 text-muted">{row.structure}</td>
                          <td className="p-3">{row.price}</td>
                          <td className={`p-3 text-right ${row.pos ? 'text-signal-strong' : 'text-negative'}`}>{row.pnl}</td>
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
          <motion.div 
            key="flow"
            initial={{ opacity: 0, x: 20 }}
            animate={{ opacity: 1, x: 0 }}
            exit={{ opacity: 0, x: -20 }}
            className="flex flex-col gap-6"
          >
            <div className="flex items-center justify-between mb-2">
              <div className="text-[10px] font-mono text-primary flex items-center gap-2 uppercase tracking-widest">
                <Shield size={12} className="text-accent" /> Flow Supports Position
              </div>
              <div className="px-2 py-0.5 border border-grid text-[9px] font-mono text-muted uppercase tracking-widest">4 POSITIONS</div>
            </div>
            
            <div className="border border-grid bg-panel/30">
              <table className="w-full text-left font-mono text-[10px] border-collapse">
                <thead>
                  <tr className="border-b border-grid text-muted">
                    <th className="p-4 font-medium uppercase tracking-widest">Ticker</th>
                    <th className="p-4 font-medium uppercase tracking-widest">Position</th>
                    <th className="p-4 font-medium uppercase tracking-widest">Flow</th>
                    <th className="p-4 font-medium uppercase tracking-widest">Strength</th>
                    <th className="p-4 font-medium uppercase tracking-widest">Signal</th>
                  </tr>
                </thead>
                <tbody>
                  {[
                    { t: "IGV", p: "Long Call", f: "77% ACCUM", s: [8, 6, 4, 7, 9], sig: "Moderate accumulation signal" },
                    { t: "MSFT", p: "Stock (1000.0 shares)", f: "76% ACCUM", s: [9, 9, 3, 5, 8], sig: "Moderate accumulation signal" },
                    { t: "NAK", p: "Stock (18628.0 shares)", f: "73% ACCUM", s: [2, 2, 7, 5, 4], sig: "Moderate accumulation signal" },
                    { t: "SOFI", p: "Long Call", f: "65% ACCUM", s: [5, 5, 5, 5, 4], sig: "Strong signal, 6-day sustained accumulation" },
                  ].map((row, i) => (
                    <tr key={i} className="border-b border-grid/50 hover:bg-panel-raised/50 transition-colors">
                      <td className="p-4 font-bold">{row.t}</td>
                      <td className="p-4 text-muted">{row.p}</td>
                      <td className="p-4">
                        <span className="px-2 py-1 border border-accent/30 text-[9px] text-accent font-bold">{row.f}</span>
                      </td>
                      <td className="p-4">
                        <div className="flex gap-1 items-end h-6">
                           {row.s.map((h, j) => (
                             <div key={j} className="w-1.5 bg-accent/50" style={{ height: `${h * 10}%` }} />
                           ))}
                        </div>
                      </td>
                      <td className="p-4 text-muted">{row.sig}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <div className="flex items-center gap-2 mt-4 text-[10px] font-mono text-muted uppercase tracking-widest">
              <ChevronRight size={12} /> Flow against position (0)
            </div>
          </motion.div>
        );
      case "PORTFOLIO":
        return (
          <motion.div 
            key="portfolio"
            initial={{ opacity: 0, x: 20 }}
            animate={{ opacity: 1, x: 0 }}
            exit={{ opacity: 0, x: -20 }}
            className="flex flex-col gap-4 overflow-hidden"
          >
            <div className="flex items-center justify-between mb-2">
              <div className="text-[10px] font-mono text-primary flex items-center gap-2 uppercase tracking-widest">
                <Activity size={12} className="text-accent" /> Defined Risk Positions
              </div>
              <div className="px-2 py-0.5 border border-grid text-[9px] font-mono text-muted uppercase tracking-widest">15 POSITIONS</div>
            </div>

            <div className="border border-grid bg-panel/30 overflow-x-auto">
              <table className="w-full text-left font-mono text-[9px] border-collapse whitespace-nowrap">
                <thead>
                  <tr className="border-b border-grid text-muted bg-panel-raised/50">
                    <th className="p-3 font-medium uppercase">Ticker</th>
                    <th className="p-3 font-medium uppercase">Structure</th>
                    <th className="p-3 font-medium uppercase">Qty</th>
                    <th className="p-3 font-medium uppercase">Underlying</th>
                    <th className="p-3 font-medium uppercase">Last</th>
                    <th className="p-3 font-medium uppercase text-right">Day Chg</th>
                    <th className="p-3 font-medium uppercase text-right">P&L</th>
                    <th className="p-3 font-medium uppercase text-right">Exp</th>
                  </tr>
                </thead>
                <tbody>
                  {[
                    { t: "AAOI", s: "Long Call $105", q: "50", u: "123.36", l: "22.20", d: "+64.08%", p: "+43,350", e: "2026-03-20" },
                    { t: "AAPL", s: "Bull Call Spread", q: "100", u: "262.15", l: "4.65", d: "+11.24%", p: "+4,700", e: "2026-04-17" },
                    { t: "ALAB", s: "Long Call $120", q: "5", u: "120.08", l: "37.55", d: "-2.80%", p: "+540", e: "2027-01-15" },
                    { t: "AMD", s: "Long Call $195", q: "20", u: "205.65", l: "49.85", d: "+4.22%", p: "+4,040", e: "2027-01-15" },
                    { t: "BRZE", s: "Long Call $22.5", q: "120", u: "19.01", l: "0.18", d: "-59.30%", p: "-11,500", e: "2026-03-20" },
                  ].map((row, i) => (
                    <tr key={i} className="border-b border-grid/30 hover:bg-panel-raised/50">
                      <td className="p-3 font-bold">{row.t}</td>
                      <td className="p-3 text-muted">{row.s}</td>
                      <td className="p-3">{row.q}</td>
                      <td className="p-3 text-signal-strong">{row.u}</td>
                      <td className="p-3 font-bold">{row.l}</td>
                      <td className={`p-3 text-right ${row.d.startsWith('+') ? 'text-signal-strong' : 'text-negative'}`}>{row.d}</td>
                      <td className={`p-3 text-right font-bold ${row.p.startsWith('+') ? 'text-signal-strong' : 'text-negative'}`}>{row.p}</td>
                      <td className="p-3 text-muted">{row.e}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </motion.div>
        );
    }
  };

  return (
    <div className="min-h-screen bg-canvas selection:bg-accent selection:text-canvas overflow-x-hidden">
      {/* Background Grid */}
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
          <motion.div 
            initial="hidden"
            animate="visible"
            variants={containerVariants}
            className="flex flex-col items-center text-center"
          >
            <motion.h1 
              variants={itemVariants}
              className="text-5xl md:text-7xl lg:text-8xl font-display font-bold mb-8 max-w-5xl leading-[1.05] tracking-tight"
            >
              Market structure, <span className="text-muted">reconstructed.</span>
            </motion.h1>

            <motion.p 
              variants={itemVariants}
              className="text-lg md:text-xl text-secondary max-w-2xl mb-12 font-sans leading-relaxed"
            >
              Radon Terminal is an institutional-grade instrument for reconstructing market structure from noisy signals. Built for the 1% of technical traders who demand sovereignty over their execution.
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
          <motion.div 
            initial={{ opacity: 0, scale: 0.95 }}
            whileInView={{ opacity: 1, scale: 1 }}
            viewport={{ once: true }}
            transition={{ duration: 1, ease: "easeOut" }}
            className="relative"
          >
            {/* Mockup Frame */}
            <div className="border border-grid bg-panel rounded-lg overflow-hidden shadow-2xl">
              <div className="h-10 border-b border-grid bg-panel-raised flex items-center justify-between px-4">
                <div className="flex gap-2">
                  <div className="w-2.5 h-2.5 rounded-full bg-grid" />
                  <div className="w-2.5 h-2.5 rounded-full bg-grid" />
                  <div className="w-2.5 h-2.5 rounded-full bg-grid" />
                </div>
                <div className="text-[10px] font-mono text-muted uppercase tracking-widest">
                  radon-terminal // live-feed: connected
                </div>
                <div className="flex items-center gap-4">
                   <div className="text-[10px] font-mono text-signal-strong uppercase tracking-widest flex items-center gap-2">
                      <div className="w-1.5 h-1.5 bg-signal-strong rounded-full animate-pulse" /> SYNC: OK
                   </div>
                   <div className="w-12" />
                </div>
              </div>
              
              <div className="p-1 flex flex-col md:flex-row gap-1 h-[650px]">
                {/* Sidebar Mock */}
                <div className="w-full md:w-48 border-r border-grid p-4 hidden md:flex flex-col gap-6 bg-canvas/40 backdrop-blur-sm">
                  <div className="space-y-6">
                    <div className="space-y-1">
                      <div className="text-[9px] font-mono text-muted uppercase tracking-widest mb-3 px-2">Workspace</div>
                      <button 
                        onClick={() => setActiveView("REGIME")}
                        className={`h-8 w-full flex items-center px-2 text-[10px] font-mono transition-all border-l-2 ${activeView === "REGIME" ? 'bg-accent/10 border-accent text-primary' : 'border-transparent text-muted hover:bg-panel-raised hover:text-secondary'}`}
                      >
                        REGIME
                      </button>
                      <button 
                        onClick={() => setActiveView("PORTFOLIO")}
                        className={`h-8 w-full flex items-center px-2 text-[10px] font-mono transition-all border-l-2 ${activeView === "PORTFOLIO" ? 'bg-accent/10 border-accent text-primary' : 'border-transparent text-muted hover:bg-panel-raised hover:text-secondary'}`}
                      >
                        PORTFOLIO
                      </button>
                      <button 
                        onClick={() => setActiveView("FLOW")}
                        className={`h-8 w-full flex items-center px-2 text-[10px] font-mono transition-all border-l-2 ${activeView === "FLOW" ? 'bg-accent/10 border-accent text-primary' : 'border-transparent text-muted hover:bg-panel-raised hover:text-secondary'}`}
                      >
                        FLOW
                      </button>
                    </div>
                  </div>
                  <div className="mt-auto border-t border-grid pt-4 space-y-4">
                    <div className="flex justify-between items-center px-2">
                      <div className="text-[8px] font-mono text-muted uppercase tracking-widest">IB Gateway</div>
                      <div className="w-2 h-2 bg-signal-strong rounded-full" />
                    </div>
                    <div className="h-1 w-full bg-grid/30 px-2" />
                  </div>
                </div>
                
                {/* Content Mock */}
                <div className="flex-1 p-6 overflow-hidden bg-canvas/30">
                  {renderMockView()}
                </div>
              </div>
            </div>
            
            {/* Decorative Spectral Lines */}
            <div className="absolute -top-10 -right-10 w-40 h-40 border border-accent/20 rounded-full opacity-20 animate-ping" />
            <div className="absolute -bottom-20 -left-20 w-80 h-80 border border-accent/10 rounded-full opacity-10" />
          </motion.div>
        </section>

        {/* Features Section */}
        <section id="features" className="max-w-7xl mx-auto px-6 mb-40">
          <div className="grid md:grid-cols-3 gap-12">
            <motion.div 
              initial={{ opacity: 0, y: 20 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true }}
              className="border-l-2 border-grid pl-8 py-4 hover:border-accent transition-colors group"
            >
              <Activity className="text-accent mb-6 group-hover:scale-110 transition-transform" size={32} />
              <h3 className="text-xl font-display font-bold mb-4 uppercase tracking-tight">Radon Flow</h3>
              <p className="text-secondary font-sans leading-relaxed">
                Decompose institutional flow into principal components. Isolate non-random signals from the noise of retail positioning.
              </p>
            </motion.div>
            
            <motion.div 
              initial={{ opacity: 0, y: 20 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true }}
              transition={{ delay: 0.1 }}
              className="border-l-2 border-grid pl-8 py-4 hover:border-accent transition-colors group"
            >
              <Zap className="text-accent mb-6 group-hover:scale-110 transition-transform" size={32} />
              <h3 className="text-xl font-display font-bold mb-4 uppercase tracking-tight">Radon Surface</h3>
              <p className="text-secondary font-sans leading-relaxed">
                Map volatility surfaces in real-time. Detect structural dislocations and convexity traps before they materialize in price action.
              </p>
            </motion.div>

            <motion.div 
              initial={{ opacity: 0, y: 20 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true }}
              transition={{ delay: 0.2 }}
              className="border-l-2 border-grid pl-8 py-4 hover:border-accent transition-colors group"
            >
              <Shield className="text-accent mb-6 group-hover:scale-110 transition-transform" size={32} />
              <h3 className="text-xl font-display font-bold mb-4 uppercase tracking-tight">Radon Structure</h3>
              <p className="text-secondary font-sans leading-relaxed">
                Reconstruct cross-asset state from fragmented data. A scientific approach to regime detection and transition probability.
              </p>
            </motion.div>
          </div>
        </section>

        {/* Open Source / Arrogance Section */}
        <section id="open-source" className="max-w-7xl mx-auto px-6 mb-40">
          <motion.div 
            initial={{ opacity: 0 }}
            whileInView={{ opacity: 1 }}
            viewport={{ once: true }}
            className="bg-panel border border-grid p-12 md:p-24 relative overflow-hidden"
          >
            <div className="relative z-10 max-w-3xl">
              <h2 className="text-4xl md:text-5xl font-display font-bold mb-8 leading-tight">
                Open Source because we aren't afraid of a code audit. <span className="text-accent underline decoration-accent/30 underline-offset-8">Are you?</span>
              </h2>
              <p className="text-xl text-secondary mb-12 font-sans leading-relaxed">
                We don't hide behind black boxes. The math is public. The execution is transparent. If you can't verify your tools, you don't own your strategy.
              </p>
              <div className="flex gap-6">
                <a href="https://github.com" className="flex items-center gap-2 font-mono text-sm font-bold uppercase tracking-widest text-primary hover:text-accent transition-colors">
                  <Github size={20} /> Inspect Source
                </a>
              </div>
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

        {/* Footer */}
        <footer className="max-w-7xl mx-auto px-6 pt-20 border-t border-grid">
          <div className="grid grid-cols-2 md:grid-cols-4 gap-12 mb-20">
            <div>
              <div className="flex items-center gap-2 mb-6">
                <img src="/brand/radon-monogram.svg" alt="Radon" className="w-4 h-4" />
                <span className="font-display font-bold text-sm tracking-tight uppercase">Radon</span>
              </div>
              <p className="text-xs font-mono text-muted leading-loose">
                RECONSTRUCTING MARKET STRUCTURE FROM NOISY SIGNALS.
              </p>
            </div>
            <div>
              <h4 className="text-[10px] font-mono font-bold text-primary uppercase tracking-[0.2em] mb-6">Instruments</h4>
              <ul className="space-y-3">
                <li><a href="#" className="text-xs font-mono text-muted hover:text-accent transition-colors">FLOW</a></li>
                <li><a href="#" className="text-xs font-mono text-muted hover:text-accent transition-colors">SURFACE</a></li>
                <li><a href="#" className="text-xs font-mono text-muted hover:text-accent transition-colors">SIGNALS</a></li>
              </ul>
            </div>
            <div>
              <h4 className="text-[10px] font-mono font-bold text-primary uppercase tracking-[0.2em] mb-6">Protocol</h4>
              <ul className="space-y-3">
                <li><a href="#" className="text-xs font-mono text-muted hover:text-accent transition-colors">DOCUMENTATION</a></li>
                <li><a href="#" className="text-xs font-mono text-muted hover:text-accent transition-colors">API SPEC</a></li>
                <li><a href="#" className="text-xs font-mono text-muted hover:text-accent transition-colors">CONTRIBUTE</a></li>
              </ul>
            </div>
            <div>
              <h4 className="text-[10px] font-mono font-bold text-primary uppercase tracking-[0.2em] mb-6">Terminal</h4>
              <button className="w-full py-3 border border-accent/50 text-accent text-[10px] font-mono font-bold uppercase tracking-[0.2em] hover:bg-accent hover:text-canvas transition-all">
                Access Live Node
              </button>
            </div>
          </div>
          <div className="pb-10 flex flex-col md:flex-row justify-between items-center gap-4">
            <span className="text-[10px] font-mono text-muted uppercase tracking-widest">
              © 2026 RADON PROTOCOL // ALL RIGHTS RESERVED
            </span>
            <div className="flex gap-8">
              <span className="text-[10px] font-mono text-muted uppercase tracking-widest flex items-center gap-2">
                <div className="w-1.5 h-1.5 bg-signal-strong rounded-full" /> System Status: Operational
              </span>
              <span className="text-[10px] font-mono text-muted uppercase tracking-widest">
                Latency: 14ms
              </span>
            </div>
          </div>
        </footer>
      </main>
    </div>
  );
}
