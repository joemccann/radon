/**
 * @vitest-environment jsdom
 *
 * The GS article keeps both generated figures under the body.
 */

import React from "react";
import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { AiCreditArticle } from "../components/AiCreditArticle";

describe("AiCreditArticle", () => {
  it("places both charts under the body", () => {
    render(<AiCreditArticle />);

    const body = document.querySelector("[data-slot=body]");
    const slot = document.querySelector("[data-slot=under-body]");
    expect(body).toBeTruthy();
    expect(slot).toBeTruthy();
    expect(slot!.compareDocumentPosition(body!) & Node.DOCUMENT_POSITION_PRECEDING).toBeTruthy();
    expect(slot!.querySelectorAll("figure")).toHaveLength(2);
    expect(document.querySelector("article img")).toBeNull();
    expect(slot!.querySelectorAll('[data-mark="softbank-hy"] line')).toHaveLength(1);
  });
});
