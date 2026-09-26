"use client";
import { useSearchParams } from "next/navigation";
import { Suspense } from "react";
import WakeGate from "@/components/WakeGate";
import { OnboardingFlow } from "@/components/OnboardingFlow";

export default function OnboardingPage() {
  return (
    <WakeGate>
      <Suspense>
        <OnboardingWithParams />
      </Suspense>
    </WakeGate>
  );
}

function OnboardingWithParams() {
  const redo = useSearchParams().get("redo") === "1";
  return <OnboardingFlow redo={redo} />;
}
