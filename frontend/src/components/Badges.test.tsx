import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ClassBadge, EventKind, RulesBadge } from "./Badges";

describe("badges", () => {
  it("shows CAPTURE_DERIVED as « À valider sur site » and hides the badge once confirmed", () => {
    const { rerender, container } = render(<RulesBadge value="CAPTURE_DERIVED" />);
    expect(screen.getByText("À valider sur site")).toBeInTheDocument();
    rerender(<RulesBadge value="CONFIRMED" />);
    expect(container).toBeEmptyDOMElement();
  });

  it("tells ACTION and INFORMATIONAL apart by word and icon, not colour alone", () => {
    render(
      <>
        <ClassBadge value="ACTION" />
        <ClassBadge value="INFORMATIONAL" />
      </>,
    );
    const action = screen.getByTestId("class-ACTION");
    const info = screen.getByTestId("class-INFORMATIONAL");
    expect(action).toHaveTextContent("Action");
    expect(info).toHaveTextContent("Information");
    expect(action.textContent).not.toBe(info.textContent);
    expect(action.className).not.toBe(info.className);
  });

  it("labels events with their class", () => {
    render(<EventKind kind="WORKFLOW_ITEM_COMPLETED" cls="INFORMATIONAL" />);
    expect(screen.getByText(/Arrivée \(information\)/)).toBeInTheDocument();
    expect(screen.getByTestId("class-INFORMATIONAL")).toBeInTheDocument();
  });
});
