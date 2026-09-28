import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import ResultCard from "@/components/ResultCard";
import type { Card } from "@/lib/types";

const card: Card = {
  key: "menu:10", name: "카페 아메리카노", brand: "스타벅스", score: 83, source: "brand_bean", confidence: "medium",
  acidity: 2, body: 3, sweetness: 2, tags: ["chocolate"], tags_ko: ["초콜릿"], is_decaf: false, order_decaf: true,
  decaf_surcharge_krw: 300, caffeine_mg: 150, is_milk: false, coffee_id: null, menu_item_id: 10, violation: null,
  template: "취향 적합도 83%.",
};

describe("ResultCard", () => {
  it("shows score, decaf order hint, source and template while the explanation is still empty", () => {
    render(<ResultCard card={card} explanation={{ text: "", status: "streaming" }} onLog={() => {}} />);
    expect(screen.getByText("83%")).toBeInTheDocument();
    expect(screen.getByText("디카페인으로 변경 +300원")).toBeInTheDocument();
    expect(screen.getByText("브랜드 원두 기준(추정)")).toBeInTheDocument();
    expect(screen.getByText("취향 적합도 83%.")).toBeInTheDocument();
    expect(screen.getByText("초콜릿")).toBeInTheDocument();
  });

  it("replaces the template with streamed text and shows a violation banner and evidence", async () => {
    const onLog = vi.fn();
    const predicted: Card = { ...card, source: "predicted", confidence: "low", n_neighbors: 10, order_decaf: false,
      violation: "디카페인이 아니에요", evidence: ["유사 원두 10개 중 8개에서 '레몬' 언급"] };
    render(<ResultCard card={predicted} explanation={{ text: "산미가 밝아요", status: "done" }} onLog={onLog} />);
    expect(screen.getByRole("alert")).toHaveTextContent("디카페인이 아니에요");
    expect(screen.getByText("산미가 밝아요")).toBeInTheDocument();
    expect(screen.queryByText("취향 적합도 83%.")).not.toBeInTheDocument();
    expect(screen.getByText("유사 원두 10개 기반 예측 · 신뢰도 낮음")).toBeInTheDocument();
    expect(screen.getByText("유사 원두 10개 중 8개에서 '레몬' 언급")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "마셔봤어요" }));
    expect(onLog).toHaveBeenCalledWith(predicted);
  });

  it("never shows the regular drink's caffeine on an order-decaf card", () => {
    const est: Card = { ...card, name: "꿀화이트 아메리카노", brand: "이디야커피", decaf_surcharge_krw: null,
      caffeine_mg: 7, caffeine_mg_note: "디카페인 주문 시 추정" };
    const { unmount } = render(<ResultCard card={est} onLog={() => {}} />);
    expect(screen.getByText("카페인 ~7mg (디카페인 주문 시 추정)")).toBeInTheDocument();
    unmount();
    render(<ResultCard card={{ ...card, caffeine_mg: null, caffeine_mg_note: "디카페인 주문 시 카페인 ↓" }} onLog={() => {}} />);
    expect(screen.getByText("디카페인 주문 시 카페인 ↓")).toBeInTheDocument();
    expect(screen.queryByText(/150mg/)).not.toBeInTheDocument();
  });

  it("falls back to a no-number label when an older API sends the regular mg without a note", () => {
    render(<ResultCard card={card} onLog={() => {}} />);          // order_decaf with caffeine_mg 150, no note
    expect(screen.getByText("디카페인 주문 시 카페인 ↓")).toBeInTheDocument();
    expect(screen.queryByText("카페인 150mg")).not.toBeInTheDocument();
  });

  it("shows a real decaf SKU's or a regular drink's own caffeine as measured", () => {
    render(<ResultCard card={{ ...card, is_decaf: true, order_decaf: false, caffeine_mg: 11.2 }} onLog={() => {}} />);
    expect(screen.getByText("카페인 11.2mg")).toBeInTheDocument();
  });
});
