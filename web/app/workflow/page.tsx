import WorkflowComposer from "./WorkflowComposer";

export const dynamic = "force-dynamic";

// F14 — Visual flow-pipeline node-graph composer. Minimal working canvas:
// loads/saves a graph and runs it server-side. Full WorkspaceShell nav-section
// integration is a follow-up (see manifest completeness note).
export default function WorkflowPage() {
  return <WorkflowComposer />;
}
