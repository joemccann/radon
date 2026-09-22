import WorkspaceShell from "@/components/WorkspaceShell";
import { routeMetadata } from "@/lib/pageTitle";

export const metadata = routeMetadata("/orders");

export default function OrdersPage() {
  return <WorkspaceShell section="orders" />;
}
