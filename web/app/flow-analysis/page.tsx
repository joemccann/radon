import WorkspaceShell from "@/components/WorkspaceShell";
import { routeMetadata } from "@/lib/pageTitle";

export const metadata = routeMetadata("/flow-analysis");

export default function FlowAnalysisPage() {
  return <WorkspaceShell section="flow-analysis" />;
}
