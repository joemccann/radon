import WorkspaceShell from "@/components/WorkspaceShell";
import { routeMetadata } from "@/lib/pageTitle";

export const metadata = routeMetadata("/regime/iei-hyg");

export default function RegimeIeiHygPage() {
  return <WorkspaceShell section="regime" />;
}
