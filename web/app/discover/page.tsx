import { redirect } from "next/navigation";

export default function DiscoverPage() {
  redirect("/scanner?mode=discover");
}
