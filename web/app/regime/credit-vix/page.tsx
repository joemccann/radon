import WorkspaceShell from "@/components/WorkspaceShell";
import { routeMetadata } from "@/lib/pageTitle";

export const metadata = routeMetadata("/regime/credit-vix");

export default function RegimeCreditVixPage() {
  return <WorkspaceShell section="regime" />;
}
