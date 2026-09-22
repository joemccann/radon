import WorkspaceShell from "@/components/WorkspaceShell";
import { routeMetadata } from "@/lib/pageTitle";

export const metadata = routeMetadata("/performance");

export default function PerformancePage() {
  return <WorkspaceShell section="performance" />;
}
