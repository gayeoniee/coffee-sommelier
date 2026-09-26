"use client";
import { useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import WakeGate from "@/components/WakeGate";
import { OnboardingFlow } from "@/components/OnboardingFlow";

export default function OnboardingPage() {
  // A returning user (back button, bookmark) gets the redo flow: the sample step would 409 for them.
  const [hasProfile, setHasProfile] = useState(false);
  return (
    <WakeGate onReady={setHasProfile}>
      <Suspense>
        <OnboardingWithParams hasProfile={hasProfile} />
      </Suspense>
    </WakeGate>
  );
}

function OnboardingWithParams({ hasProfile }: { hasProfile: boolean }) {
  const redo = useSearchParams().get("redo") === "1" || hasProfile;
  return <OnboardingFlow redo={redo} />;
}
