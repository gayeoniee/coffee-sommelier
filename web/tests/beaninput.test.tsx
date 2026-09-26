import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import BeanInput from "@/components/BeanInput";

afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); });

describe("BeanInput", () => {
  it("debounces search, lets the user pick a hit, or submit free text", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify([
      { id: 7, name: "Ethiopia Yirgacheffe", roaster: "R", origin_country: "Ethiopia", is_decaf: false },
    ]), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    const onPick = vi.fn();
    const onSubmit = vi.fn();
    render(<BeanInput onPick={onPick} onSubmit={onSubmit} />);
    const input = screen.getByRole("searchbox");
    fireEvent.change(input, { target: { value: "예" } });
    fireEvent.change(input, { target: { value: "예가" } });
    await act(async () => { vi.advanceTimersByTime(300); });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0][0]).toBe(`/api/coffees/search?q=${encodeURIComponent("예가")}`);
    fireEvent.click(await screen.findByText("Ethiopia Yirgacheffe"));
    expect(onPick).toHaveBeenCalledWith(expect.objectContaining({ id: 7 }));
    fireEvent.change(input, { target: { value: "동네 하우스 블렌드" } });
    fireEvent.submit(input.closest("form")!);
    expect(onSubmit).toHaveBeenCalledWith("동네 하우스 블렌드");
  });
});
