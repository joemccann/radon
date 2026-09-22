import WorkspaceShell from "@/components/WorkspaceShell";
import { routeMetadata } from "@/lib/pageTitle";

export const metadata = routeMetadata("/regime/cot");

export default function RegimeCotPage() {
  return <WorkspaceShell section="regime" />;
}
