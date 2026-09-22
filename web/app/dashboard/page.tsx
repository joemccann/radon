import WorkspaceShell from "@/components/WorkspaceShell";
import { routeMetadata } from "@/lib/pageTitle";

export const metadata = routeMetadata("/dashboard");

export default function DashboardPage() {
  return <WorkspaceShell section="dashboard" />;
}
