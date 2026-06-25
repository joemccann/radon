import type { ReactNode } from "react";

type EditorialEyebrowProps = {
  children: ReactNode;
  className?: string;
};

export function EditorialEyebrow({ children, className }: EditorialEyebrowProps) {
  return (
    <span className={["editorial-eyebrow", className].filter(Boolean).join(" ")}>
      {children}
    </span>
  );
}
