let meta = #{
    name: "ui-ux-skill-stack",
    description: "Run Impeccable + Vercel + Emil + pro-max in sequence to improve Radon UI with screenshots",
    when_to_use: "Radically improve app.radon.run UI/UX using the installed skill stack with visual proof",
    phases: [
        #{ title: "Baseline", detail: "Desktop+mobile screenshots of key surfaces" },
        #{ title: "Audit", detail: "Parallel skill audits (read-only)" },
        #{ title: "Prioritize", detail: "Synthesize P0-P2 plan under brand lock" },
        #{ title: "Implement", detail: "Ship high-impact CSS/shell/component fixes" },
        #{ title: "Motion", detail: "Emil motion pass on interactive chrome" },
        #{ title: "Verify", detail: "After screenshots + tests + report" },
    ],
};

// Brand lock injected into every agent prompt (Radon non-negotiables)
let brand = "";
brand += "RADON BRAND LOCK (never violate):\n";
brand += "- docs/brand-identity.md + brand/radon-brand-system.md are law.\n";
brand += "- Canvas #0a0f14, panel #0f1519, line #2e3947, signal.core #05AD98.\n";
brand += "- Inter UI + IBM Plex Mono numbers. Max 4px radius on panels; badges may capsule.\n";
brand += "- NO glassmorphism, soft shadows, gradients, decorative emojis, em dashes in user copy.\n";
brand += "- Hairline borders, matte surfaces, instrument modules, device-label headers.\n";
brand += "- Empty states describe measurement conditions.\n";
brand += "- Surgical changes only; no drive-by refactors outside UI chrome/globals/shared shell.\n";
brand += "- Prefer web/app/globals.css + shared components (Header, Sidebar, WorkspaceShell, ScannerInstrumentShell, SectionEmptyState, Modal).\n";
brand += "- Do not invent a new design system via ui-ux-pro-max palettes.\n";

// ── Phase 1: baseline screenshots ────────────────────────────────
phase("Baseline");

let baseline_prompt = "";
baseline_prompt += brand;
baseline_prompt += "\nTASK: Capture BASELINE screenshots of the Radon web app BEFORE UI changes.\n";
baseline_prompt += "Dev server should be at http://localhost:3000 (RADON_AUTHLESS_TEST may be set).\n";
baseline_prompt += "Use Chrome CDP skill at ~/.claude/skills/chrome-cdp/scripts/cdp.mjs if available.\n";
baseline_prompt += "Output dir: docs/design-shots/skill-stack/baseline/\n";
baseline_prompt += "Capture DESKTOP viewport (~1440x900) and MOBILE (set viewport 390x844 or device metrics):\n";
baseline_prompt += "1) /scanner?mode=theta\n";
baseline_prompt += "2) /scanner?mode=garch\n";
baseline_prompt += "3) /dashboard or / (main dashboard)\n";
baseline_prompt += "4) /portfolio if reachable\n";
baseline_prompt += "Name files: baseline-desktop-{page}.png and baseline-mobile-{page}.png\n";
baseline_prompt += "Also write docs/design-shots/skill-stack/BASELINE.md listing files + what they show.\n";
baseline_prompt += "If CDP fails, use Playwright from web/ with a one-off shot script.\n";
baseline_prompt += "Return: list of screenshot paths created.\n";

let r0 = agent(baseline_prompt, #{
    label: "baseline-shots",
    capability_mode: "all",
    agent_type: "general-purpose",
});
log("Baseline phase finished");

// ── Phase 2: parallel audits ─────────────────────────────────────
phase("Audit");

let audit_ux = "";
audit_ux += brand;
audit_ux += "\nYou are applying the web-design-guidelines skill (Vercel Web Interface Guidelines).\n";
audit_ux += "Read ~/.agents/skills/web-design-guidelines/SKILL.md fully, then audit Radon web UI.\n";
audit_ux += "Focus: web/components/Header.tsx, Sidebar.tsx, WorkspaceShell.tsx, Modal.tsx, ScannerInstrumentShell.tsx,\n";
audit_ux += "SectionEmptyState.tsx, web/app/globals.css (shared chrome only).\n";
audit_ux += "Produce docs/design-shots/skill-stack/AUDIT-web-design-guidelines.md with P0/P1/P2 findings.\n";
audit_ux += "Max 15 findings. Each: severity, file, issue, concrete fix. Read-only — no code edits.\n";
audit_ux += "Return summary of top 5 P0/P1 items.\n";

let audit_perf = "";
audit_perf += brand;
audit_perf += "\nYou are applying vercel-react-best-practices.\n";
audit_perf += "Read ~/.agents/skills/vercel-react-best-practices/SKILL.md and rules/.\n";
audit_perf += "Audit Next.js web/ for high-impact UI-adjacent perf issues that hurt UX (waterfalls, huge client bundles on scanner, missing suspense).\n";
audit_perf += "Scope: web/app/scanner, web/components/WorkspaceShell.tsx, web/lib/use*.ts for scanner hooks.\n";
audit_perf += "Write docs/design-shots/skill-stack/AUDIT-react-best-practices.md — top 10 prioritized fixes.\n";
audit_perf += "Read-only. Return top 5.\n";

let audit_motion = "";
audit_motion += brand;
audit_motion += "\nYou are applying find-animation-opportunities + emil-design-eng judgment.\n";
audit_motion += "Read ~/.agents/skills/find-animation-opportunities/SKILL.md and ~/.agents/skills/emil-design-eng/SKILL.md.\n";
audit_motion += "Survey web/app/globals.css interactive chrome and key components for motion that helps vs noise.\n";
audit_motion += "Write docs/design-shots/skill-stack/AUDIT-motion.md with: should-animate list (exact values) and should-not-animate list.\n";
audit_motion += "Read-only. Max 12 opportunities. Return summary.\n";

let audit_taste = "";
audit_taste += brand;
audit_taste += "\nYou are applying impeccable + frontend-design critique (not greenfield redesign).\n";
audit_taste += "Read ~/.agents/skills/impeccable/SKILL.md and frontend-design skill.\n";
audit_taste += "Critique the instrument-module grammar consistency across scanners, dashboard snapshot cards, and shell.\n";
audit_taste += "Write docs/design-shots/skill-stack/AUDIT-impeccable.md: what feels generic vs instrument-true; 8 concrete visual upgrades that stay on-brand.\n";
audit_taste += "Read-only. Return the 8 upgrades as a bullet list.\n";

let audits = parallel([
    #{ prompt: audit_ux, label: "audit-web-guidelines", capability_mode: "all", agent_type: "general-purpose" },
    #{ prompt: audit_perf, label: "audit-react-perf", capability_mode: "all", agent_type: "general-purpose" },
    #{ prompt: audit_motion, label: "audit-motion", capability_mode: "all", agent_type: "general-purpose" },
    #{ prompt: audit_taste, label: "audit-impeccable", capability_mode: "all", agent_type: "general-purpose" },
]);

log("Audit phase finished");

// ── Phase 3: prioritize ──────────────────────────────────────────
phase("Prioritize");

let prio_prompt = "";
prio_prompt += brand;
prio_prompt += "\nRead all AUDIT-*.md files under docs/design-shots/skill-stack/.\n";
prio_prompt += "Synthesize a single plan: docs/design-shots/skill-stack/PLAN.md\n";
prio_prompt += "Select at most 12 implementable items as P0/P1 that:\n";
prio_prompt += "1) Improve UX radically in shared chrome (not one-off pages only)\n";
prio_prompt += "2) Are safe under brand lock\n";
prio_prompt += "3) Can ship in one session without rewriting the product\n";
prio_prompt += "Group: Shell, Scanner modules, Motion, Perf, A11y.\n";
prio_prompt += "Write checkable tasks T1..Tn. Do not implement yet.\n";
prio_prompt += "Return the plan path and T1-Tn titles.\n";

let r_plan = agent(prio_prompt, #{
    label: "prioritize",
    capability_mode: "all",
    agent_type: "general-purpose",
});
log("Prioritize phase finished");

// ── Phase 4: implement ───────────────────────────────────────────
phase("Implement");

let impl_prompt = "";
impl_prompt += brand;
impl_prompt += "\nImplement ALL P0 and P1 items from docs/design-shots/skill-stack/PLAN.md.\n";
impl_prompt += "Apply impeccable polish + web-design-guidelines fixes surgically.\n";
impl_prompt += "Primary touch surfaces: web/app/globals.css, Header, Sidebar, WorkspaceShell, Modal, ScannerInstrumentShell, SectionEmptyState, FooterTelemetryStrip, scanner mode tabs.\n";
impl_prompt += "Extend instrument-section grammar if PLAN requires (edge gauge, device headers, meta rails).\n";
impl_prompt += "Update/add focused Vitest for any component behavior change.\n";
impl_prompt += "Run focused vitest for touched tests. Do NOT commit or push.\n";
impl_prompt += "Update PLAN.md checkboxes as you complete items.\n";
impl_prompt += "Return: files changed + test results.\n";

let r_impl = agent(impl_prompt, #{
    label: "implement-ui",
    capability_mode: "all",
    agent_type: "general-purpose",
});
if r_impl == () || !r_impl.success {
    complete(#{
        ok: false,
        phase: "Implement",
        error: "Implement agent failed",
        detail: if r_impl == () { "null" } else { r_impl.output },
    });
}
log("Implement phase finished");

// ── Phase 5: motion ──────────────────────────────────────────────
phase("Motion");

let motion_prompt = "";
motion_prompt += brand;
motion_prompt += "\nApply emil-design-eng + improve-animations judgment to IMPLEMENT (not just plan) the motion items from PLAN.md and AUDIT-motion.md.\n";
motion_prompt += "Read ~/.agents/skills/emil-design-eng/SKILL.md and improve-animations/SKILL.md.\n";
motion_prompt += "Only high-value interruptible CSS transitions on primary actions, modal open, scan buttons, nav active states.\n";
motion_prompt += "Respect prefers-reduced-motion (already global). No flashy scale-from-0. No animating layout properties.\n";
motion_prompt += "Keep press-scale at 0.96. Specific transition properties only (never transition: all).\n";
motion_prompt += "Do NOT commit. Return files changed.\n";

let r_motion = agent(motion_prompt, #{
    label: "motion-pass",
    capability_mode: "all",
    agent_type: "general-purpose",
});
log("Motion phase finished");

// ── Phase 6: verify screenshots ──────────────────────────────────
phase("Verify");

let verify_prompt = "";
verify_prompt += brand;
verify_prompt += "\nVERIFY the UI skill-stack work with screenshots and tests.\n";
verify_prompt += "1) Capture AFTER shots to docs/design-shots/skill-stack/after/:\n";
verify_prompt += "   desktop + mobile for scanner?mode=theta, scanner?mode=garch, dashboard/home.\n";
verify_prompt += "   Use CDP at http://localhost:3000 (or start next with RADON_AUTHLESS_TEST=1 if needed).\n";
verify_prompt += "2) Run focused vitest: scanner + shell related tests.\n";
verify_prompt += "3) Write docs/design-shots/skill-stack/REPORT.md with:\n";
verify_prompt += "   - Before/after file pairs\n";
verify_prompt += "   - What each skill contributed\n";
verify_prompt += "   - Diff summary (git diff --stat on web/)\n";
verify_prompt += "   - Test results\n";
verify_prompt += "   - Residual gaps\n";
verify_prompt += "4) open -R the after folder in Finder.\n";
verify_prompt += "5) Do NOT commit unless all tests green — then still do NOT push; leave commit to user preference.\n";
verify_prompt += "Return REPORT path and screenshot list.\n";

let r_verify = agent(verify_prompt, #{
    label: "verify-shots",
    capability_mode: "all",
    agent_type: "general-purpose",
});

let report = "";
report += "# UI/UX skill-stack workflow result\n\n";
report += "## Baseline\n";
if r0 != () && r0.success { report += r0.output; } else { report += "(baseline agent failed or empty)"; }
report += "\n\n## Audits\n";
let i = 0;
while i < audits.len() {
    if audits[i] != () && audits[i].success {
        report += "\n### audit " + i.to_string() + "\n" + audits[i].output + "\n";
    }
    i += 1;
}
report += "\n## Plan\n";
if r_plan != () && r_plan.success { report += r_plan.output; }
report += "\n## Implement\n";
if r_impl != () && r_impl.success { report += r_impl.output; }
report += "\n## Motion\n";
if r_motion != () && r_motion.success { report += r_motion.output; }
report += "\n## Verify\n";
if r_verify != () && r_verify.success { report += r_verify.output; }

let path = write_scratch_file("ui-ux-skill-stack-result.md", report);
complete(#{
    ok: true,
    path: path,
    summary: "Skill-stack UI/UX pass complete — see docs/design-shots/skill-stack/REPORT.md",
});
