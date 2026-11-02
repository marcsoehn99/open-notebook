import quizPollIncludesQueued from "./quiz-poll-includes-queued.mjs"
import noSetQueryDataOutsideHooks from "./no-set-query-data-outside-hooks.mjs"
import noRefWriteDuringRender from "./no-ref-write-during-render.mjs"

const plugin = {
  meta: { name: "reviewer", version: "1.0.0" },
  rules: {
    "quiz-poll-includes-queued": quizPollIncludesQueued,
    "no-set-query-data-outside-hooks": noSetQueryDataOutsideHooks,
    "no-ref-write-during-render": noRefWriteDuringRender,
  },
}

export default plugin
