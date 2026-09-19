import WorkspaceShell from "@/components/WorkspaceShell";
import { routeMetadata } from "@/lib/pageTitle";

export const metadata = routeMetadata("/regime/panic-index");

export default function RegimePanicIndexPage() {
  return <WorkspaceShell section="regime" />;
}
