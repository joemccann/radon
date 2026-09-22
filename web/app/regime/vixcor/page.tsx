import WorkspaceShell from "@/components/WorkspaceShell";
import { routeMetadata } from "@/lib/pageTitle";

export const metadata = routeMetadata("/regime/vixcor");

export default function RegimeVixcorPage() {
  return <WorkspaceShell section="regime" />;
}
