import type { ProfileInput } from "./api";
import type { CaffeineRule } from "./types";

export type OnboardingDraft = {
  caffeine_rule: CaffeineRule;
  milk_ok: boolean;
  acidity: number;
  body: number;
  sweetness: number;
  likes: string[];
};

export const defaultDraft: OnboardingDraft = { caffeine_rule: "any", milk_ok: true, acidity: 3, body: 3, sweetness: 3, likes: [] };

export function profileInput(d: OnboardingDraft, nickname?: string): ProfileInput {
  const body: ProfileInput = {
    caffeine_rule: d.caffeine_rule, milk_ok: d.milk_ok, acidity: d.acidity, body: d.body, sweetness: d.sweetness,
    flavor_likes: d.likes,
  };
  const nick = nickname?.trim();
  if (nick) body.nickname = nick;
  return body;
}

export function sampleAnswers(choices: Record<number, boolean | undefined>): { coffee_id: number; liked: boolean }[] {
  return Object.entries(choices)
    .filter(([, v]) => v !== undefined)
    .map(([id, v]) => ({ coffee_id: Number(id), liked: v as boolean }));
}
