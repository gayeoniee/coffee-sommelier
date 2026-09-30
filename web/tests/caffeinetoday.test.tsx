import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import CaffeineToday from "@/components/CaffeineToday";

describe("CaffeineToday", () => {
  it("renders nothing with no data yet", () => {
    const { container } = render(<CaffeineToday today={null} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("renders nothing when nothing was drunk and no limit is set", () => {
    const { container } = render(<CaffeineToday today={{ today_mg: 0, unknown_count: 0, limit_mg: null, remaining_mg: null }} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("shows the running total alone when no limit is set", () => {
    render(<CaffeineToday today={{ today_mg: 150, unknown_count: 0, limit_mg: null, remaining_mg: null }} />);
    expect(screen.getByText("150mg")).toBeInTheDocument();
    expect(screen.queryByText(/남음/)).not.toBeInTheDocument();
  });

  it("shows the limit, remaining mg, and a bar when a limit is set", () => {
    render(<CaffeineToday today={{ today_mg: 150, unknown_count: 1, limit_mg: 300, remaining_mg: 150 }} />);
    expect(screen.getByText("150mg / 300mg")).toBeInTheDocument();
    expect(screen.getByText("남음 150mg")).toBeInTheDocument();
    expect(screen.getByText("카페인 정보 없는 기록 1건은 빼고 계산했어요")).toBeInTheDocument();
  });

  it("flags an over-budget day instead of a negative remaining", () => {
    render(<CaffeineToday today={{ today_mg: 350, unknown_count: 0, limit_mg: 300, remaining_mg: -50 }} />);
    expect(screen.getByText("오늘 한도를 넘었어요")).toBeInTheDocument();
  });
});
