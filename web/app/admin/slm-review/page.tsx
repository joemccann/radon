import { notFound, redirect } from "next/navigation";
import { requireRouteAccess } from "@/lib/routeAccess";
import { routeMetadata } from "@/lib/pageTitle";
import SlmReview from "./slm-review";
import styles from "./slm-review.module.css";

export const metadata = routeMetadata("/admin/slm-review");
export const dynamic = "force-dynamic";

export default async function SlmReviewPage() {
  const access = await requireRouteAccess(undefined, { operatorOnly: true });
  if (!access.ok) {
    if (access.response.status === 401) redirect("/sign-in");
    notFound();
  }

  return (
    <main className={styles.page}>
      <SlmReview reviewer={access.principal.userId} />
    </main>
  );
}
