import WorkspaceShell from "@/components/WorkspaceShell";

export const dynamic = "force-dynamic";

// F14 — Visual flow-pipeline node-graph composer, reachable from the nav and
// wrapped in WorkspaceShell so it carries the standard header + status chrome.
// WorkspaceSections renders <WorkflowComposer/> for the "workflow" section.
export default function WorkflowPage() {
  return <WorkspaceShell section="workflow" />;
}
