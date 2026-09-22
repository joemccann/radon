import { redirect } from "next/navigation";
import { routeMetadata } from "@/lib/pageTitle";

export const metadata = routeMetadata("/regime");

export default function RegimePage() {
  redirect("/regime/cri");
}
