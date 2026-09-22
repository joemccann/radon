import { redirect } from "next/navigation";
import { routeMetadata } from "@/lib/pageTitle";

export const metadata = routeMetadata("/internals");

export default function InternalsPage() {
  redirect("/regime");
}
