import WorkspaceShell from "@/components/WorkspaceShell";
import { requireRouteAccess } from "@/lib/routeAccess";
import { notFound, redirect } from "next/navigation";

export const dynamic = "force-dynamic";

export default async function AdminPage() {
  const access = await requireRouteAccess(undefined, { operatorOnly: true });
  if (!access.ok) {
    if (access.response.status === 401) redirect("/sign-in");
    notFound();
  }
  return <WorkspaceShell section="admin" />;
}
