import WorkspaceShell from "@/components/WorkspaceShell";
import { routeMetadata } from "@/lib/pageTitle";

export const metadata = routeMetadata("/regime/quant");

export default function RegimeQuantPage() {
  return <WorkspaceShell section="regime" />;
}
