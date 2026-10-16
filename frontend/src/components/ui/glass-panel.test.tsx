import { render, screen } from "@testing-library/react"
import { describe, expect, it } from "vitest"

import { GlassPanel } from "./glass-panel"

describe("GlassPanel", () => {
  it("renders its children as a glass accent", () => {
    render(<GlassPanel>Dein Ziel</GlassPanel>)

    const panel = screen.getByText("Dein Ziel")

    expect(panel.tagName).toBe("DIV")
    expect(panel.className).toContain("srh-glass")
    expect(panel.className).not.toContain("srh-glass-strong")
  })

  it("uses the stronger blur and the requested element", () => {
    render(
      <GlassPanel as="section" strong>
        Campus
      </GlassPanel>
    )

    const panel = screen.getByText("Campus")

    expect(panel.tagName).toBe("SECTION")
    expect(panel.className).toContain("srh-glass")
    expect(panel.className).toContain("srh-glass-strong")
  })
})
