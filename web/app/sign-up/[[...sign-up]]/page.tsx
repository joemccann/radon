import { SignUp } from "@clerk/nextjs";
import { redirect } from "next/navigation";
import { isWebAuthBypassEnabled } from "@/lib/webAuthMode";

export default function SignUpPage() {
  if (isWebAuthBypassEnabled()) {
    redirect("/");
  }

  return (
    <div style={{ display: "flex", justifyContent: "center", alignItems: "center", minHeight: "100vh" }}>
      <SignUp />
    </div>
  );
}
