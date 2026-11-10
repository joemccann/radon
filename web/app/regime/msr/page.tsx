import WorkspaceShell from "@/components/WorkspaceShell";
import { routeMetadata } from "@/lib/pageTitle";

export const metadata = routeMetadata("/regime/msr");

export default function RegimeMsrPage() {
  return <WorkspaceShell section="regime" />;
}
