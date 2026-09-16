import { redirect } from "next/navigation";
import { routeMetadata } from "@/lib/pageTitle";

export const metadata = routeMetadata("/preferences");

export const dynamic = "force-dynamic";

export default function PreferencesPage() {
  redirect("/profile?tab=preferences");
}
