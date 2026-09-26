export default function Stars({ value, onChange }: { value: number; onChange: (v: number) => void }) {
  return (
    <div className="flex gap-1">
      {[1, 2, 3, 4, 5].map((n) => (
        <button key={n} type="button" aria-label={`${n}점`} aria-pressed={value === n} onClick={() => onChange(n)}
          className={`h-11 w-11 text-2xl ${n <= value ? "text-accent" : "text-crema"}`}>★</button>
      ))}
    </div>
  );
}
