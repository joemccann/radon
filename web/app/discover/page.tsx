import { redirect } from "next/navigation";
import { routeMetadata } from "@/lib/pageTitle";

export const metadata = routeMetadata("/discover");

export default function DiscoverPage() {
  redirect("/scanner?mode=discover");
}
