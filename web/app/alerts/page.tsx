import WorkspaceShell from "@/components/WorkspaceShell";
import { routeMetadata } from "@/lib/pageTitle";

export const metadata = routeMetadata("/alerts");

export const dynamic = "force-dynamic";

export default function AlertsPage() {
  return <WorkspaceShell section="alerts" />;
}
