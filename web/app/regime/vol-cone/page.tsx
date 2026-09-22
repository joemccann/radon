import { redirect } from "next/navigation";
import { routeMetadata } from "@/lib/pageTitle";

export const metadata = routeMetadata("/regime/vol-cone");

export default function RegimeVolConePage() {
  redirect("/scanner?mode=vol-cone");
}
