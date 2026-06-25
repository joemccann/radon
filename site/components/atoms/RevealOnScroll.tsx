"use client";

import { useEffect, useRef, useState, type ElementType, type ReactNode } from "react";

type RevealOnScrollProps = {
  children: ReactNode;
  as?: ElementType;
  className?: string;
  id?: string;
};

// Adds the editorial `.reveal` -> `.reveal.in` transition via IntersectionObserver,
// matching the mockup (threshold 0.12, rootMargin bottom -8%). Honors reduced motion
// by revealing immediately, consistent with the .reveal CSS in globals.css.
export function RevealOnScroll({
  children,
  as,
  className,
  id,
}: RevealOnScrollProps) {
  const Tag = as ?? "div";
  const ref = useRef<HTMLElement | null>(null);
  const [shown, setShown] = useState(false);

  useEffect(() => {
    const node = ref.current;
    if (!node) return;

    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reduce || !("IntersectionObserver" in window)) {
      // Reveal on the next frame so it stays out of the effect body. Under
      // reduced motion the .reveal CSS already pins opacity, so this just clears
      // the initial transform without a cascading synchronous render.
      const frame = requestAnimationFrame(() => setShown(true));
      return () => cancelAnimationFrame(frame);
    }

    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) {
            setShown(true);
            observer.unobserve(entry.target);
          }
        }
      },
      { threshold: 0.12, rootMargin: "0px 0px -8% 0px" },
    );

    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  return (
    <Tag
      ref={ref}
      id={id}
      className={["reveal", shown ? "in" : "", className].filter(Boolean).join(" ")}
    >
      {children}
    </Tag>
  );
}
