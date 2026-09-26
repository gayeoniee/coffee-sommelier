"use client";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { ApiError, api } from "@/lib/api";
import { defaultDraft, profileInput, sampleAnswers, type OnboardingDraft } from "@/lib/onboarding";
import { ATTR_KO, RULE_KO } from "@/lib/labels";
import { CHIPS, type CaffeineRule, type Sample } from "@/lib/types";

export function OnboardingFlow({ redo }: { redo: boolean }) {
  const router = useRouter();
  const lastStep = redo ? 2 : 3;
  const [step, setStep] = useState(1);
  const [draft, setDraft] = useState<OnboardingDraft>(defaultDraft);
  const [samples, setSamples] = useState<Sample[]>([]);
  const [choices, setChoices] = useState<Record<number, boolean | undefined>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (step === 3) api.samples().then(setSamples).catch(() => setSamples([]));
  }, [step]);

  async function finish() {
    setBusy(true);
    setError(null);
    try {
      await api.putProfile(profileInput(draft));
      const answers = sampleAnswers(choices);
      if (!redo && answers.length > 0) await api.postSamples(answers);
      router.push("/");
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "저장하지 못했어요");
      setBusy(false);
    }
  }

  const set = <K extends keyof OnboardingDraft>(k: K, v: OnboardingDraft[K]) => setDraft((d) => ({ ...d, [k]: v }));

  return (
    <div>
      <p className="text-sm text-roast">{step} / {lastStep}</p>
      {step === 1 && (
        <section>
          <h1 className="mt-1 text-2xl font-bold">꼭 지켜야 할 조건</h1>
          <fieldset className="mt-5">
            <legend className="mb-2 text-sm text-roast">카페인</legend>
            <div role="radiogroup" className="grid grid-cols-3 gap-2">
              {(Object.keys(RULE_KO) as CaffeineRule[]).map((r) => (
                <button key={r} role="radio" aria-checked={draft.caffeine_rule === r} onClick={() => set("caffeine_rule", r)}
                  className={`min-h-11 rounded-xl ring-1 ${draft.caffeine_rule === r ? "bg-espresso text-cream ring-espresso" : "bg-white/70 ring-crema"}`}>
                  {RULE_KO[r]}
                </button>
              ))}
            </div>
          </fieldset>
          <label className="mt-5 flex min-h-11 items-center justify-between rounded-xl bg-white/70 px-4 ring-1 ring-crema">
            <span>우유 들어간 음료도 괜찮아요</span>
            <input type="checkbox" checked={draft.milk_ok} onChange={(e) => set("milk_ok", e.target.checked)} className="h-5 w-5" />
          </label>
        </section>
      )}
      {step === 2 && (
        <section>
          <h1 className="mt-1 text-2xl font-bold">어떤 맛을 좋아하세요?</h1>
          {(["acidity", "body", "sweetness"] as const).map((k) => (
            <label key={k} className="mt-5 block">
              <span className="flex justify-between text-sm"><span>{ATTR_KO[k]}</span><span className="tabular-nums">{draft[k].toFixed(1)}</span></span>
              <input type="range" min={1} max={5} step={0.5} value={draft[k]} onChange={(e) => set(k, Number(e.target.value))}
                aria-label={ATTR_KO[k]} className="mt-2 w-full accent-roast" />
            </label>
          ))}
          <p className="mt-6 text-sm text-roast">좋아하는 향미 (여러 개)</p>
          <div className="mt-2 flex flex-wrap gap-2">
            {CHIPS.map((c) => {
              const on = draft.likes.includes(c.key);
              return (
                <button key={c.key} aria-pressed={on}
                  onClick={() => set("likes", on ? draft.likes.filter((x) => x !== c.key) : [...draft.likes, c.key])}
                  className={`min-h-11 rounded-full px-4 ring-1 ${on ? "bg-roast text-cream ring-roast" : "bg-white/70 ring-crema"}`}>
                  {c.ko}
                </button>
              );
            })}
          </div>
        </section>
      )}
      {step === 3 && (
        <section>
          <h1 className="mt-1 text-2xl font-bold">이 커피들은 어때요?</h1>
          <p className="mt-1 text-sm text-roast">답하지 않아도 괜찮아요.</p>
          <ul className="mt-4 space-y-3">
            {samples.map((s) => (
              <li key={s.coffee_id} className="rounded-2xl bg-white/80 p-4 ring-1 ring-crema">
                <p className="font-semibold">{s.name}</p>
                <p className="mt-1 text-sm text-roast">{s.description}</p>
                <div className="mt-3 grid grid-cols-2 gap-2">
                  {([true, false] as const).map((liked) => (
                    <button key={String(liked)} aria-label={`${s.name} ${liked ? "좋아요" : "별로"}`} aria-pressed={choices[s.coffee_id] === liked}
                      onClick={() => setChoices((c) => ({ ...c, [s.coffee_id]: c[s.coffee_id] === liked ? undefined : liked }))}
                      className={`min-h-11 rounded-xl ring-1 ${choices[s.coffee_id] === liked ? "bg-espresso text-cream ring-espresso" : "bg-white ring-crema"}`}>
                      {liked ? "좋아요" : "별로"}
                    </button>
                  ))}
                </div>
              </li>
            ))}
          </ul>
        </section>
      )}
      {error && <p className="mt-4 text-sm text-warn">{error}</p>}
      <div className="mt-8 flex gap-2">
        {step > 1 && <button onClick={() => setStep(step - 1)} className="min-h-11 flex-1 rounded-full bg-white ring-1 ring-crema">이전</button>}
        {step < lastStep ? (
          <button onClick={() => setStep(step + 1)} className="min-h-11 flex-1 rounded-full bg-espresso text-cream">다음</button>
        ) : (
          <button onClick={finish} disabled={busy} className="min-h-11 flex-1 rounded-full bg-accent font-semibold text-white disabled:opacity-40">
            {redo ? "저장" : "시작하기"}
          </button>
        )}
      </div>
    </div>
  );
}
