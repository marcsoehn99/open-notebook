/**
 * Quiz polling must keep going for `queued` (and must not treat `unknown` as terminal).
 *
 * Finding: 2026-10-16-quiz-me.md S1. A refetch helper that continues only for
 * `new` / `running` shows a still-queued job as failed and stops polling.
 */
function isFunction(node) {
  return (
    node.type === "FunctionDeclaration" ||
    node.type === "FunctionExpression" ||
    node.type === "ArrowFunctionExpression"
  )
}

function walkOwnBody(root, visit) {
  const stack = [root]
  while (stack.length > 0) {
    const node = stack.pop()
    if (!node || typeof node !== "object") {
      continue
    }
    if (node !== root && isFunction(node)) {
      continue
    }
    visit(node)
    for (const key of Object.keys(node)) {
      if (key === "parent") {
        continue
      }
      const child = node[key]
      if (Array.isArray(child)) {
        for (const item of child) {
          if (item && typeof item === "object" && item.type) {
            stack.push(item)
          }
        }
      } else if (child && typeof child === "object" && child.type) {
        stack.push(child)
      }
    }
  }
}

function isStatusEquality(node, literal) {
  if (!node || node.type !== "BinaryExpression") {
    return false
  }
  if (node.operator !== "===" && node.operator !== "==") {
    return false
  }
  return [node.left, node.right].some(
    (side) => side.type === "Literal" && side.value === literal,
  )
}

function bodyHasStatus(fn, literal) {
  let found = false
  walkOwnBody(fn.body ?? fn, (node) => {
    if (isStatusEquality(node, literal)) {
      found = true
    }
  })
  return found
}

function isPollFunction(fn) {
  let returnsNumber = false
  let returnsFalse = false
  let returnsIdentifier = false
  walkOwnBody(fn.body ?? fn, (node) => {
    if (node.type !== "ReturnStatement" || !node.argument) {
      return
    }
    if (node.argument.type === "Literal" && typeof node.argument.value === "number") {
      returnsNumber = true
    }
    if (node.argument.type === "Literal" && node.argument.value === false) {
      returnsFalse = true
    }
    if (node.argument.type === "Identifier") {
      returnsIdentifier = true
    }
  })
  return returnsNumber || (returnsIdentifier && returnsFalse)
}

function consequentReturnsFalse(statement) {
  if (!statement) {
    return false
  }
  if (statement.type === "ReturnStatement") {
    return (
      statement.argument &&
      statement.argument.type === "Literal" &&
      statement.argument.value === false
    )
  }
  if (statement.type === "BlockStatement") {
    return statement.body.some(
      (item) =>
        item.type === "ReturnStatement" &&
        item.argument &&
        item.argument.type === "Literal" &&
        item.argument.value === false,
    )
  }
  return false
}

const rule = {
  meta: {
    type: "problem",
    docs: {
      description:
        "Quiz/job polling that mentions new or running must also keep polling queued, and must not stop on unknown (2026-10-16-quiz-me.md S1).",
    },
    schema: [],
    messages: {
      queued:
        "Polling continues for new/running but never mentions queued, so a queued job looks failed (2026-10-16-quiz-me.md S1). Treat queued like running.",
      unknown:
        "Polling stops when status is unknown (2026-10-16-quiz-me.md S1). Unknown means the status lookup failed; keep polling until completed or failed.",
    },
  },
  create(context) {
    function check(fn) {
      if (!isPollFunction(fn)) {
        return
      }
      const mentionsRunning = bodyHasStatus(fn, "running")
      const mentionsNew = bodyHasStatus(fn, "new")
      const mentionsQueued = bodyHasStatus(fn, "queued")
      if ((mentionsRunning || mentionsNew) && !mentionsQueued) {
        context.report({ node: fn, messageId: "queued" })
      }
      walkOwnBody(fn.body ?? fn, (node) => {
        if (node.type !== "IfStatement" || !isStatusEquality(node.test, "unknown")) {
          return
        }
        if (consequentReturnsFalse(node.consequent)) {
          context.report({ node, messageId: "unknown" })
        }
      })
    }

    return {
      FunctionDeclaration: check,
      FunctionExpression: check,
      ArrowFunctionExpression: check,
    }
  },
}

export default rule
