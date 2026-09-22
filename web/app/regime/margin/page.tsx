import WorkspaceShell from "@/components/WorkspaceShell";
import { routeMetadata } from "@/lib/pageTitle";

export const metadata = routeMetadata("/regime/margin");

export default function RegimeMarginPage() {
  return <WorkspaceShell section="regime" />;
}
