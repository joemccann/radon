import { SignIn } from "@clerk/nextjs";
import { auth } from "@clerk/nextjs/server";
import { redirect } from "next/navigation";
import AuthFrame from "@/components/AuthFrame";

export default async function SignInPage() {
  const { userId } = await auth();

  if (userId) {
    redirect("/portfolio");
  }

  return (
    <AuthFrame>
      <SignIn />
    </AuthFrame>
  );
}
