"use client";
import { useCallback, useEffect, useState } from "react";
import { ApiError, api } from "@/lib/api";

const SLOW_MS = 2500;

export function useSession() {
  const [state, setState] = useState<"booting" | "slow" | "ready" | "error">("booting");
  const [hasProfile, setHasProfile] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let alive = true;
    const slow = setTimeout(() => alive && setState((s) => (s === "booting" ? "slow" : s)), SLOW_MS);
    api.session()
      .then((s) => {
        if (!alive) return;
        setHasProfile(s.has_profile);
        setState("ready");
      })
      .catch((e: unknown) => {
        if (!alive) return;
        setError(e instanceof ApiError ? e.message : "서버에 연결할 수 없어요");
        setState("error");
      })
      .finally(() => clearTimeout(slow));
    return () => {
      alive = false;
      clearTimeout(slow);
    };
  }, [attempt]);

  const retry = useCallback(() => {
    setState("booting");
    setError(null);
    setAttempt((n) => n + 1);
  }, []);
  return { state, hasProfile, error, retry };
}
