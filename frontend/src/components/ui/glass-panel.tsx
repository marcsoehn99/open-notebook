import * as React from "react"

import { cn } from "@/lib/utils"

const tags = {
  div: "div",
  section: "section",
  aside: "aside",
} as const

type GlassTag = keyof typeof tags

type GlassPanelProps = {
  as?: GlassTag
  strong?: boolean
  children?: React.ReactNode
  className?: string
  style?: React.CSSProperties
}

export function GlassPanel({
  as = "div",
  strong = false,
  children,
  className,
  style,
}: GlassPanelProps) {
  const Tag = tags[as]

  return (
    <Tag
      className={cn("srh-glass", strong && "srh-glass-strong", className)}
      style={style}
    >
      {children}
    </Tag>
  )
}
