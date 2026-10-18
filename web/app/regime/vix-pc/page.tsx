import WorkspaceShell from "@/components/WorkspaceShell";
import { routeMetadata } from "@/lib/pageTitle";

export const metadata = routeMetadata("/regime/vix-pc");

export default function RegimeVixPcPage() {
  return <WorkspaceShell section="regime" />;
}
