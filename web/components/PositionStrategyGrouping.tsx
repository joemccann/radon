"use client";

import { useEffect, useId, useLayoutEffect, useState } from "react";
import { createPortal } from "react-dom";
import { Layers, X } from "lucide-react";
import type { PortfolioPosition } from "@/lib/types";
import type { PriceData } from "@/lib/pricesProtocol";
import { describeStrategyLegs, strategySourceIdentity, type StrategyGroup } from "@/lib/positionStrategyGroups";
import { fmtUsd, getInitialValue, getPnlDollars, resolveMarketValue, resolveRealtimeMarketValue } from "@/lib/positionUtils";
import { useDialogChrome } from "@/lib/useDialogChrome";
import ErrorToast from "./ErrorToast";

export type PositionStrategyGroupingControls = {
  sourceFor: (row: PortfolioPosition) => PortfolioPosition;
  groupsFor: (source: PortfolioPosition) => StrategyGroup[];
  suggestionFor: (source: PortfolioPosition) => StrategyGroup[] | null;
  preview: (source: PortfolioPosition, groups: StrategyGroup[]) => PortfolioPosition[] | null;
  apply: (source: PortfolioPosition, groups: StrategyGroup[]) => boolean;
  reset: (source: PortfolioPosition) => boolean;
  undo: () => boolean;
  canUndo: boolean;
};

export function groupingControlsEqual(
  prev?: PositionStrategyGroupingControls,
  next?: PositionStrategyGroupingControls,
): boolean {
  if (prev === next) return true;
  if (!prev || !next) return false;
  return prev.sourceFor === next.sourceFor
    && prev.groupsFor === next.groupsFor
    && prev.suggestionFor === next.suggestionFor
    && prev.preview === next.preview
    && prev.apply === next.apply
    && prev.reset === next.reset
    && prev.undo === next.undo
    && prev.canUndo === next.canUndo;
}

type Mode = "menu" | "manual" | "editor" | "preview";
function money(value: number | null): string {
  return value == null || !Number.isFinite(value) ? "Unavailable" : `${value < 0 ? "-" : ""}${fmtUsd(Math.abs(value))}`;
}

export default function PositionStrategyGrouping({ position, controls, prices }: {
  position: PortfolioPosition;
  controls: PositionStrategyGroupingControls;
  prices?: Record<string, PriceData>;
}) {
  const source = controls.sourceFor(position);
  const legs = describeStrategyLegs(source);
  const [mode, setMode] = useState<Mode | null>(null);
  const [previousMode, setPreviousMode] = useState<Mode>("menu");
  const [draft, setDraft] = useState<StrategyGroup[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [groupName, setGroupName] = useState("Selected spread");
  const [anchor, setAnchor] = useState({ left: 16, top: 100 });
  const [error, setError] = useState<string | null>(null);
  const titleId = useId();
  const close = () => setMode(null);
  const { portalTarget, panelRef } = useDialogChrome<HTMLDivElement>({ open: mode != null, onClose: close });
  const identity = strategySourceIdentity(source)?.fingerprint;
  useEffect(() => { setMode(null); setSelected([]); }, [identity]);
  useEffect(() => { if (mode) panelRef.current?.focus(); }, [mode, panelRef]);
  useLayoutEffect(() => {
    if (mode !== "menu" || !portalTarget || !panelRef.current) return;
    const panel = panelRef.current;
    const fit = () => {
      if (window.innerWidth <= 640) return;
      const bounds = panel.getBoundingClientRect();
      setAnchor(current => {
        const left = Math.max(12, Math.min(current.left, window.innerWidth - bounds.width - 12));
        const top = Math.max(12, Math.min(current.top, window.innerHeight - bounds.height - 12));
        return left === current.left && top === current.top ? current : { left, top };
      });
    };
    fit();
    window.addEventListener("resize", fit);
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(fit);
    observer?.observe(panel);
    return () => { window.removeEventListener("resize", fit); observer?.disconnect(); };
  }, [mode, portalTarget, panelRef]);
  if (legs.length < 2) return null;
  const suggestion = controls.suggestionFor(source);
  const currentGroups = controls.groupsFor(source);
  const hasGrouping = currentGroups.length > 1 || currentGroups[0]?.name !== source.structure;
  const preview = mode === "preview" ? controls.preview(source, draft) : null;
  const legLabel = (leg: typeof legs[number]["leg"]) => `${leg.direction} ${leg.contracts} ${leg.type} $${leg.strike}`;
  const beginPreview = (groups: StrategyGroup[], origin: Mode) => {
    setError(null);
    if (controls.preview(source, groups) == null) {
      setError("Assign every held leg to one named strategy before previewing.");
      return;
    }
    setDraft(groups); setPreviousMode(origin); setMode("preview");
  };
  const manualGroups = () => {
    const matching = suggestion?.find(group => group.legIds.length === selected.length && group.legIds.every(id => selected.includes(id)));
    return [
      { id: "selected", name: groupName === "Selected spread" ? matching?.name ?? groupName : groupName, legIds: selected },
      ...legs.filter(({ id }) => !selected.includes(id)).map(({ id, leg }, index) => ({ id: `remaining-${index}`, name: `${leg.direction === "SHORT" ? "Short" : "Long"} ${leg.type}`, legIds: [id] })),
    ];
  };
  const apply = () => {
    if (controls.apply(source, draft)) close();
    else setError("Holdings changed. Review the current legs and try grouping again.");
  };
  const title = mode === "editor" ? `Edit ${source.ticker} strategies`
    : mode === "manual" ? `Select ${source.ticker} legs`
    : mode === "preview" ? `Preview ${source.ticker} grouping` : `Group ${source.ticker} strategies`;
  return <span className="strategy-group-control" onClick={event => event.stopPropagation()} onKeyDown={event => { if (event.key === "Enter" || event.key === " ") event.stopPropagation(); }}>
    <button type="button" className="strategy-group-trigger" aria-label={`Group strategies for ${source.ticker}`} aria-haspopup="dialog" aria-expanded={mode != null}
      onClick={event => {
        const box = event.currentTarget.getBoundingClientRect();
        setAnchor({ left: box.left, top: box.bottom + 8 });
        setError(null); setMode("menu");
      }}><Layers size={15} aria-hidden="true" /></button>
    {error && <ErrorToast message={error} />}
    {mode && portalTarget && createPortal(<div className={`strategy-group-overlay strategy-group-overlay--${mode}`} onClick={event => { if (event.target === event.currentTarget) close(); }}>
      <div ref={panelRef} tabIndex={-1} role="dialog" aria-modal="true" aria-labelledby={titleId}
        className={`strategy-group-panel strategy-group-panel--${mode}`}
        style={mode === "menu" ? { left: anchor.left, top: Math.max(12, anchor.top) } : undefined}
        data-testid={`grouping-${mode}`}>
        <header className="strategy-group-header"><h2 id={titleId}>{title}</h2><button type="button" onClick={close} aria-label="Close grouping"><X size={18} aria-hidden="true" /></button></header>
        <div className="strategy-group-body">
          {mode === "menu" && <>
            <p>Organize held legs into strategies.</p>
            <button type="button" className="strategy-group-choice" disabled={!suggestion} onClick={() => suggestion && beginPreview(suggestion, "menu")}><strong>Suggested split</strong><span>{suggestion ? suggestion.map(group => group.name).join(" + ") : "No clear split for these legs"}</span></button>
            <button type="button" className="strategy-group-choice" onClick={() => { setSelected([]); setGroupName("Selected spread"); setMode("manual"); }}><strong>Select legs</strong><span>Choose the legs to group as a spread</span></button>
            <button type="button" className="strategy-group-choice" onClick={() => { setDraft(controls.groupsFor(source).map(group => ({ ...group, legIds: [...group.legIds] }))); setMode("editor"); }}><strong>Edit strategies</strong><span>Name groups and assign every leg</span></button>
          </>}
          {mode === "manual" && <>
            <p>Select at least two held legs. Remaining legs keep their own rows.</p>
            <div className="strategy-group-leg-list">{legs.map(({ id, leg }) => <label key={id} className="strategy-group-leg"><input type="checkbox" checked={selected.includes(id)} aria-label={`Select ${legLabel(leg)}`} onChange={event => setSelected(current => event.target.checked ? [...current, id] : current.filter(value => value !== id))} /><span>{legLabel(leg)}<small>{leg.expiry ?? source.expiry}</small></span></label>)}</div>
            <label className="strategy-group-field">Spread name<input value={groupName} onChange={event => setGroupName(event.target.value)} maxLength={80} /></label>
          </>}
          {mode === "editor" && <>
            <p>Assign each held leg to one named group.</p>
            <div className="strategy-group-names">{draft.map((group, index) => <label className="strategy-group-field" key={group.id}>Strategy name {index + 1}<input value={group.name} aria-label={`Strategy name ${index + 1}`} maxLength={80} onChange={event => setDraft(current => current.map(item => item.id === group.id ? { ...item, name: event.target.value } : item))} /></label>)}</div>
            <button type="button" className="strategy-group-secondary" disabled={draft.length >= legs.length} onClick={() => setDraft(current => [...current, { id: `custom-${crypto.randomUUID()}`, name: `Strategy ${current.length + 1}`, legIds: [] }])}>Add strategy</button>
            <div className="strategy-group-leg-list">{legs.map(({ id, leg }) => <label key={id} className="strategy-group-assignment"><span>{legLabel(leg)}<small>{leg.expiry ?? source.expiry}</small></span><select aria-label={`Strategy group for ${legLabel(leg)}`} value={draft.find(group => group.legIds.includes(id))?.id ?? ""} onChange={event => setDraft(current => current.map(group => ({ ...group, legIds: group.id === event.target.value ? [...group.legIds.filter(value => value !== id), id] : group.legIds.filter(value => value !== id) })))}>{draft.map(group => <option key={group.id} value={group.id}>{group.name || "Unnamed strategy"}</option>)}</select></label>)}</div>
          </>}
          {mode === "preview" && <>
            <p>Review the strategy rows before applying.</p>
            {preview?.map((row, index) => <section className="strategy-group-preview-row" key={draft[index]?.id ?? index}><h3>{draft[index]?.name ?? row.structure}</h3>{row.structure_type !== (draft[index]?.name ?? row.structure) && <p>{row.structure_type}</p>}<dl><div><dt>Quantity</dt><dd>{row.contracts}</dd></div><div><dt>Initial value</dt><dd>{money(getInitialValue(row))}</dd></div><div><dt>P&amp;L</dt><dd>{money(getPnlDollars(row, resolveRealtimeMarketValue(row, prices) ?? resolveMarketValue(row)))}</dd></div></dl><ul>{row.legs.map((leg, legIndex) => <li key={legIndex}>{legLabel(leg)}</li>)}</ul></section>)}
            <p className="strategy-group-note">Grouping changes the strategy rows shown in your portfolio. Held contracts and account totals are preserved.</p>
          </>}
        </div>
        <footer className={`strategy-group-footer${mode === "manual" ? " strategy-group-selection-bar" : ""}`}>
          {mode === "menu" ? <button type="button" className="strategy-group-secondary" disabled={!hasGrouping} onClick={() => { if (controls.reset(source)) close(); }}>Reset grouping</button>
            : <><button type="button" className="strategy-group-secondary" onClick={() => mode === "preview" ? setMode(previousMode) : close()}>{mode === "preview" ? "Back" : "Cancel"}</button>
              {mode === "manual" && <><span>{selected.length} selected</span><button type="button" className="strategy-group-primary" disabled={selected.length < 2 || !groupName.trim()} onClick={() => beginPreview(manualGroups(), "manual")}>Group as spread</button></>}
              {mode === "editor" && <button type="button" className="strategy-group-primary" disabled={draft.some(group => !group.name.trim())} onClick={() => beginPreview(draft.filter(group => group.legIds.length > 0), "editor")}>Preview grouping</button>}
              {mode === "preview" && <><button type="button" className="strategy-group-secondary" onClick={close}>Cancel</button><button type="button" className="strategy-group-primary" disabled={!preview} onClick={apply}>Apply grouping</button></>}</>}
        </footer>
      </div>
    </div>, portalTarget)}
  </span>;
}
