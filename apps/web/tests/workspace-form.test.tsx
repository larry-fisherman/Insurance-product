import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { Field, Form } from "../src/components/workspace/primitives";

it("protects a dirty review form when a tab or route action would discard it", () => {
  const navigate = vi.fn();
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
  render(
    <>
      <Form action={async () => undefined}>
        <Field title="Correction reason" name="reason" />
      </Form>
      <button data-navigation onClick={navigate}>
        Another tab
      </button>
    </>,
  );
  fireEvent.change(screen.getByLabelText("Correction reason"), {
    target: { value: "Unsaved client confirmation" },
  });
  fireEvent.click(screen.getByText("Another tab"));
  expect(confirm).toHaveBeenCalled();
  expect(navigate).not.toHaveBeenCalled();
  expect(screen.getByLabelText("Correction reason")).toHaveValue(
    "Unsaved client confirmation",
  );
});

it("clears the dirty guard only after a successful save", async () => {
  const navigate = vi.fn();
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
  render(
    <>
      <Form action={async () => undefined}>
        <Field title="Review reason" name="reason" />
      </Form>
      <button data-navigation onClick={navigate}>
        Open next tab
      </button>
    </>,
  );
  fireEvent.change(screen.getByLabelText("Review reason"), {
    target: { value: "Saved client confirmation" },
  });
  fireEvent.click(screen.getByText("Save changes"));
  await waitFor(() => expect(screen.getByText("Saved successfully")).toBeInTheDocument());
  fireEvent.click(screen.getByText("Open next tab"));
  expect(navigate).toHaveBeenCalledOnce();
  expect(confirm).not.toHaveBeenCalled();
});

it("allows saving another form while retaining the first form's dirty input", async () => {
  const saveOther = vi.fn(async () => undefined);
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
  render(
    <>
      <Form action={async () => undefined}>
        <Field title="Unsaved first form" name="first" />
      </Form>
      <Form action={saveOther}>
        <Field title="Other form" name="other" />
      </Form>
    </>,
  );
  fireEvent.change(screen.getByLabelText("Unsaved first form"), {
    target: { value: "Keep this unsaved note" },
  });
  fireEvent.click(screen.getAllByText("Save changes")[1]);
  await waitFor(() => expect(saveOther).toHaveBeenCalledOnce());
  expect(confirm).not.toHaveBeenCalled();
  expect(screen.getByLabelText("Unsaved first form")).toHaveValue(
    "Keep this unsaved note",
  );
});
