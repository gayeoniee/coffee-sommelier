import type { Brand } from "@/lib/types";

export default function BrandPicker({ brands, selected, onPick }: { brands: Brand[]; selected: string | null; onPick: (b: Brand) => void }) {
  return (
    <div className="grid grid-cols-3 gap-2">
      {brands.map((b) => (
        <button key={b.key} onClick={() => onPick(b)}
          className={`min-h-11 rounded-xl px-2 py-2 text-sm ring-1 ${selected === b.key ? "bg-espresso text-cream ring-espresso" : "bg-white/70 ring-crema"}`}>
          {b.name}
          {!b.decaf_available && <span className="block text-[10px] opacity-70">디카페인 없음</span>}
        </button>
      ))}
    </div>
  );
}
