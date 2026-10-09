"use client";

import { useCallback, useEffect, useId, useRef, useState } from "react";
import {
  RELEASE_POLL_MS,
  RELEASE_VERSION_PATH,
  parseRelease,
  releaseStamp,
  releaseState,
  runningRelease,
  type ReleaseIdentity,
  type ReleaseState,
} from "@/lib/releaseStatus";
import styles from "./ReleaseStatus.module.css";

type ReleaseStatusProps = {
  placement?: "header" | "drawer";
  reload?: () => void;
};

function defaultReload() {
  window.location.reload();
}

function latestLabel(state: ReleaseState, latest: ReleaseIdentity | null): string {
  if (state === "local") return "Local build";
  if (state === "unknown" || !latest) return "Latest not read";
  return latest.version;
}

export default function ReleaseStatus({ placement = "header", reload = defaultReload }: ReleaseStatusProps) {
  const running = runningRelease();
  const panelId = useId();
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const [open, setOpen] = useState(false);
  const [latest, setLatest] = useState<ReleaseIdentity | null>(null);
  const [failed, setFailed] = useState(false);
  const [announce, setAnnounce] = useState("");
  const state = releaseState(running, latest, failed);
  const previous = useRef(state);

  const pull = useCallback(async (signal?: AbortSignal) => {
    try {
      const response = await fetch(RELEASE_VERSION_PATH, {
        method: "GET",
        cache: "no-store",
        headers: { accept: "application/json" },
        signal,
      });
      if (!response.ok) {
        setFailed(true);
        setLatest(null);
        return;
      }
      const parsed = parseRelease(await response.json());
      if (!parsed) {
        setFailed(true);
        setLatest(null);
        return;
      }
      setFailed(false);
      setLatest(parsed);
    } catch (error) {
      if (signal?.aborted) return;
      if (error instanceof DOMException && error.name === "AbortError") return;
      setFailed(true);
      setLatest(null);
    }
  }, []);

  useEffect(() => {
    if (previous.current !== "update" && state === "update") setAnnounce("Update available");
    previous.current = state;
  }, [state]);

  useEffect(() => {
    if (running.channel !== "production") return;
    const controller = new AbortController();
    const load = () => {
      if (document.visibilityState === "hidden") return;
      void pull(controller.signal);
    };
    load();
    const timer = window.setInterval(load, RELEASE_POLL_MS);
    const onVisible = () => {
      if (document.visibilityState === "visible") load();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      controller.abort();
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [pull, running.channel, running.sha]);

  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setOpen(false);
        triggerRef.current?.focus();
      }
    };
    const onPointer = (event: PointerEvent) => {
      if (!wrapRef.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("pointerdown", onPointer);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("pointerdown", onPointer);
    };
  }, [open]);

  const rows = (
    <>
      <div className={styles.row}><span className={styles.key}>Running</span><span className={styles.value}>{running.version}</span></div>
      <div className={styles.row}><span className={styles.key}>Build</span><span className={styles.value} title={running.sha}>{releaseStamp(running)}</span></div>
      <div className={styles.row}><span className={styles.key}>Latest</span><span className={styles.value}>{latestLabel(state, latest)}</span></div>
      <div className={styles.row}>
        <span className={styles.key}>Deployed</span>
        <span className={styles.value} title={latest?.sha}>{state === "current" || state === "update" ? (latest ? releaseStamp(latest) : "---") : "---"}</span>
      </div>
      {state === "update" ? (
        <button type="button" className={styles.reload} onClick={reload}>Reload</button>
      ) : null}
    </>
  );

  if (placement === "drawer") {
    return (
      <div data-testid="release-status" data-state={state} data-placement="drawer">
        <div className="mobile-drawer__status">
          <span>Release</span>
          <span className="mobile-drawer__status-value">{running.version}{state === "local" ? " local" : ""}</span>
        </div>
        <div className="mobile-drawer__status">
          <span>Latest</span>
          <span className="mobile-drawer__status-value">{latestLabel(state, latest)}</span>
        </div>
        {state === "update" ? (
          <button type="button" className={`mobile-drawer__link ${styles.drawerReload}`} onClick={reload}>Reload</button>
        ) : null}
        <span className={styles.live} aria-live="polite">{announce}</span>
      </div>
    );
  }

  const label = state === "update"
    ? `Release ${running.version}, update available`
    : state === "local"
      ? `Release ${running.version}, local build`
      : state === "current"
        ? `Release ${running.version}, current`
        : `Release ${running.version}, latest not read`;

  return (
    <div className={styles.wrap} ref={wrapRef} data-testid="release-status" data-state={state}>
      <button
        ref={triggerRef}
        type="button"
        className={styles.trigger}
        data-state={state}
        aria-expanded={open}
        aria-controls={panelId}
        aria-label={label}
        onClick={() => setOpen((value) => !value)}
      >
        <span className={styles.dot} aria-hidden />
        <span className={styles.version}>{running.version}</span>
        {state === "local" ? <span className={styles.suffix}>local</span> : null}
      </button>
      {open ? (
        <div id={panelId} className={styles.panel} role="group" aria-label="Release">
          {rows}
        </div>
      ) : null}
      <span className={styles.live} aria-live="polite">{announce}</span>
    </div>
  );
}
