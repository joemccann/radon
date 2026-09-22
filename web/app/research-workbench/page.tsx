import WorkspaceShell from "@/components/WorkspaceShell";
import { routeMetadata } from "@/lib/pageTitle";

export const metadata = routeMetadata("/research-workbench");

export const dynamic = "force-dynamic";

export default function ResearchWorkbenchPage() {
  return <WorkspaceShell section="research-workbench" />;
}
