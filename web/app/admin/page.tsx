import WorkspaceShell from "@/components/WorkspaceShell";
import { requireDemoAdmin } from "@/lib/demo/adminAuth";
import { notFound } from "next/navigation";

export const dynamic = "force-dynamic";

export default async function AdminPage() {
  if (!await requireDemoAdmin()) notFound();
  return <WorkspaceShell section="admin" />;
}
