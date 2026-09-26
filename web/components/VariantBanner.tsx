// Shown only on the competition submission deployment (Vercel env NEXT_PUBLIC_VARIANT=open),
// never on the full deployment, so judges know which data set they're looking at.
export default function VariantBanner() {
  if (process.env.NEXT_PUBLIC_VARIANT !== "open") return null;
  return (
    <p role="note" className="bg-roast/10 px-4 py-1.5 text-center text-xs text-roast">
      공모전 제출본 · 오픈 데이터 + 국내 로스터리 사실정보 (coffeereview 미포함)
    </p>
  );
}
