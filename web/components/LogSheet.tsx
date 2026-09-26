import type { Card, Profile } from "@/lib/types";
// Temporary stub so app/page.tsx (Task 5) builds; replaced by the real
// implementation in Task 6.
// eslint-disable-next-line @typescript-eslint/no-unused-vars
export default function LogSheet(_: { card: Card; onClose: () => void; onSaved: (p: Profile) => void }) {
  return null;
}
