import { SignUp } from "@clerk/nextjs";
import { auth } from "@clerk/nextjs/server";
import { redirect } from "next/navigation";
import { shouldMarkDemoSignup } from "@/lib/demo/demoSignup";
import AuthFrame from "@/components/AuthFrame";

export default async function SignUpPage() {
  const { userId } = await auth();

  if (userId) {
    redirect("/portfolio");
  }

  return (
    <AuthFrame>
      <SignUp unsafeMetadata={shouldMarkDemoSignup() ? { demo: true } : undefined} />
    </AuthFrame>
  );
}
