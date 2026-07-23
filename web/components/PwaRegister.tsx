"use client";

import { useEffect } from "react";

export default function PwaRegister() {
  useEffect(() => {
    if (typeof navigator === "undefined") return;
    if (!("serviceWorker" in navigator)) return;
    if (process.env.NODE_ENV !== "production") return;

    const register = () => {
      // updateViaCache: "none" revalidates importScripts deps (sw-decisions.js)
      // on every update check so a deploy never strands clients on a stale
      // decisions script.
      navigator.serviceWorker.register("/sw.js", { scope: "/", updateViaCache: "none" }).catch((err) => {
        console.warn("[radon] service worker registration failed:", err);
      });
    };

    if (document.readyState === "complete") {
      register();
    } else {
      window.addEventListener("load", register, { once: true });
      return () => window.removeEventListener("load", register);
    }
  }, []);

  return null;
}
