import WorkspaceShell from "@/components/WorkspaceShell";
import { routeMetadata } from "@/lib/pageTitle";

export const metadata = routeMetadata("/regime/skew2d");

export default function RegimeSkew2dPage() {
  return <WorkspaceShell section="regime" />;
}
