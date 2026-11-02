/**
 * Cache writes belong in TanStack Query hooks, not in components.
 *
 * Finding: 2026-10-19-quiz-neu.md Q-S1. `queryClient.setQueryData` in
 * QuizDialog bypassed `frontend/AGENTS.md` (mutations live in `src/lib/hooks`).
 * Pre-existing call sites may use `eslint-disable-next-line` with a comment
 * that cites this finding.
 */
const rule = {
  meta: {
    type: "problem",
    docs: {
      description:
        "setQueryData is only allowed in src/lib/hooks (2026-10-19-quiz-neu.md Q-S1).",
    },
    schema: [],
    messages: {
      outside:
        "queryClient.setQueryData belongs in src/lib/hooks (2026-10-19-quiz-neu.md Q-S1). Move the cache update into a mutation hook.",
    },
  },
  create(context) {
    return {
      CallExpression(node) {
        const callee = node.callee
        if (!callee || callee.type !== "MemberExpression") {
          return
        }
        const property = callee.property
        const name =
          property.type === "Identifier"
            ? property.name
            : property.type === "Literal"
              ? property.value
              : null
        if (name !== "setQueryData") {
          return
        }
        const filename = (context.filename || context.getFilename() || "").replaceAll(
          "\\",
          "/",
        )
        if (
          filename.includes("/src/lib/hooks/") ||
          filename.includes(".test.") ||
          filename.includes(".spec.")
        ) {
          return
        }
        context.report({ node, messageId: "outside" })
      },
    }
  },
}

export default rule
