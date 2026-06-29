import { SignUp } from "@clerk/nextjs";
import { auth } from "@clerk/nextjs/server";
import { redirect } from "next/navigation";
import { shouldMarkDemoSignup } from "@/lib/demo/demoSignup";

export default async function SignUpPage() {
  const { userId } = await auth();

  if (userId) {
    redirect("/portfolio");
  }

  return (
    <div style={{ display: "flex", justifyContent: "center", alignItems: "center", minHeight: "100vh" }}>
      <SignUp unsafeMetadata={shouldMarkDemoSignup() ? { demo: true } : undefined} />
    </div>
  );
}
