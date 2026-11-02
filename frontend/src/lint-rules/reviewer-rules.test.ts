import tsParser from '@typescript-eslint/parser'
import { type ESLint, Linter } from 'eslint'
import { readFileSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

import reviewerPlugin from '../../../lint-rules/eslint/index.mjs'

const here = path.dirname(fileURLToPath(import.meta.url))

function lint(code: string, ruleId: string, filename: string) {
  const linter = new Linter({ configType: 'flat', cwd: '/' })
  return linter.verify(
    code,
    [
      {
        files: ['**/*.ts', '**/*.tsx'],
        plugins: { reviewer: reviewerPlugin as unknown as ESLint.Plugin },
        rules: { [ruleId]: 'error' },
        languageOptions: {
          parser: tsParser,
          ecmaVersion: 2022,
          sourceType: 'module',
        },
      },
    ],
    { filename }
  )
}

const pollRule = 'reviewer/quiz-poll-includes-queued'
const cacheRule = 'reviewer/no-set-query-data-outside-hooks'
const refRule = 'reviewer/no-ref-write-during-render'

describe('reviewer/quiz-poll-includes-queued', () => {
  it('flags polling that stops before queued', () => {
    const messages = lint(
      `function quizRefetchIntervalMs(status) {
        if (status === 'new' || status === 'running') return 2000
        if (status === 'unknown') return false
        return false
      }`,
      pollRule,
      'frontend/src/lib/types/quiz.ts'
    )
    expect(messages.map((message) => message.messageId).sort()).toEqual(['queued', 'unknown'])
    expect(messages.some((message) => message.message.includes('2026-10-16-quiz-me.md S1'))).toBe(true)
  })

  it('accepts polling until completed or failed', () => {
    const messages = lint(
      `const QUIZ_POLL_MS = 2000
      function quizRefetchIntervalMs(status) {
        if (status === 'completed' || status === 'failed') return false
        return QUIZ_POLL_MS
      }`,
      pollRule,
      'frontend/src/lib/types/quiz.ts'
    )
    expect(messages).toEqual([])
  })

  it('accepts an explicit queued status', () => {
    const messages = lint(
      `function poll(status) {
        if (status === 'new' || status === 'queued' || status === 'running') return 2000
        return false
      }`,
      pollRule,
      'frontend/src/lib/hooks/use-sources.ts'
    )
    expect(messages).toEqual([])
  })
})

describe('reviewer/no-set-query-data-outside-hooks', () => {
  it('flags a component and allows a hook', () => {
    const code = 'queryClient.setQueryData(key, value)\n'
    const bad = lint(code, cacheRule, '/repo/frontend/src/components/notebooks/QuizDialog.tsx')
    const good = lint(code, cacheRule, '/repo/frontend/src/lib/hooks/use-quizzes.ts')
    expect(bad).toHaveLength(1)
    expect(bad[0]?.message).toContain('2026-10-19-quiz-neu.md Q-S1')
    expect(good).toEqual([])
  })
})

describe('reviewer/no-ref-write-during-render', () => {
  it('flags a render write and allows an effect', () => {
    const bad = lint(
      `function QuizDialog() {
        noticePin.current = quizId
        return null
      }`,
      refRule,
      'frontend/src/components/notebooks/QuizDialog.tsx'
    )
    const good = lint(
      `function QuizDialog() {
        React.useEffect(() => {
          noticePin.current = quizId
        }, [quizId])
        return null
      }`,
      refRule,
      'frontend/src/components/notebooks/QuizDialog.tsx'
    )
    expect(bad).toHaveLength(1)
    expect(bad[0]?.message).toContain('2026-10-19-quiz-neu.md Q-S2')
    expect(good).toEqual([])
  })
})

describe('shipped quiz polling', () => {
  it('matches the good pattern', () => {
    const source = readFileSync(path.join(here, '../lib/types/quiz.ts'), 'utf8')
    expect(lint(source, pollRule, path.join(here, '../lib/types/quiz.ts'))).toEqual([])
  })
})
