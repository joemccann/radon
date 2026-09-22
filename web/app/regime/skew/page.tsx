import WorkspaceShell from "@/components/WorkspaceShell";
import { routeMetadata } from "@/lib/pageTitle";

export const metadata = routeMetadata("/regime/skew");

export default function RegimeSkewPage() {
  return <WorkspaceShell section="regime" />;
}
