/**
 * Do not write `ref.current` while a function component is rendering.
 *
 * Finding: 2026-10-19-quiz-neu.md Q-S2. QuizDialog wrote `noticePin.current`
 * in the render path. React can run that render twice in Strict Mode and the
 * pin logic then disagrees with itself. Writes inside an effect, a callback,
 * or another nested function are fine.
 */
function isComponentFunction(fn) {
  if (fn.type === "FunctionDeclaration" && fn.id && /^[A-Z]/.test(fn.id.name)) {
    return true
  }
  if (fn.type === "FunctionExpression" || fn.type === "ArrowFunctionExpression") {
    const parent = fn.parent
    if (
      parent &&
      parent.type === "VariableDeclarator" &&
      parent.id.type === "Identifier" &&
      /^[A-Z]/.test(parent.id.name)
    ) {
      return true
    }
  }
  return false
}

function enclosingFunction(node) {
  let current = node.parent
  while (current) {
    if (
      current.type === "FunctionDeclaration" ||
      current.type === "FunctionExpression" ||
      current.type === "ArrowFunctionExpression"
    ) {
      return current
    }
    current = current.parent
  }
  return null
}

function isRefCurrent(expression) {
  return (
    expression &&
    expression.type === "MemberExpression" &&
    !expression.computed &&
    expression.property.type === "Identifier" &&
    expression.property.name === "current"
  )
}

const rule = {
  meta: {
    type: "problem",
    docs: {
      description:
        "Disallow ref.current writes in a function component's render path (2026-10-19-quiz-neu.md Q-S2).",
    },
    schema: [],
    messages: {
      write:
        "ref.current is written while the component renders (2026-10-19-quiz-neu.md Q-S2). Keep the value in state, or write the ref from an effect or event handler.",
    },
  },
  create(context) {
    function reportIfRenderWrite(node, target) {
      if (!isRefCurrent(target)) {
        return
      }
      const fn = enclosingFunction(node)
      if (isComponentFunction(fn)) {
        context.report({ node, messageId: "write" })
      }
    }

    return {
      AssignmentExpression(node) {
        reportIfRenderWrite(node, node.left)
      },
      UpdateExpression(node) {
        reportIfRenderWrite(node, node.argument)
      },
    }
  },
}

export default rule
