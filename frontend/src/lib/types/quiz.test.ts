import { describe, expect, it } from 'vitest'

import { quizErrorDetail, quizIsFailed, quizRefetchIntervalMs } from './quiz'

const translate = (key: string) => key

describe('quizRefetchIntervalMs', () => {
  it('polls every non-terminal status', () => {
    expect(quizRefetchIntervalMs('new')).toBe(2000)
    expect(quizRefetchIntervalMs('queued')).toBe(2000)
    expect(quizRefetchIntervalMs('running')).toBe(2000)
    expect(quizRefetchIntervalMs('unknown')).toBe(2000)
    expect(quizRefetchIntervalMs(undefined)).toBe(2000)
  })

  it('stops only when the quiz is completed or failed', () => {
    expect(quizRefetchIntervalMs('completed')).toBe(false)
    expect(quizRefetchIntervalMs('failed')).toBe(false)
  })
})

describe('quizIsFailed', () => {
  it('treats only failed as a failure', () => {
    expect(quizIsFailed('failed')).toBe(true)
    expect(quizIsFailed('queued')).toBe(false)
    expect(quizIsFailed('unknown')).toBe(false)
    expect(quizIsFailed(undefined)).toBe(false)
  })
})

describe('quizErrorDetail', () => {
  it('translates known codes and hides raw model text', () => {
    expect(quizErrorDetail(translate, 'invalid_output')).toBe('quiz.errorInvalidOutput')
    expect(quizErrorDetail(translate, 'timeout')).toBe('quiz.errorTimeout')
    expect(quizErrorDetail(translate, '1 validation error for GeneratedDeck')).toBeNull()
    expect(quizErrorDetail(translate, 'failed')).toBeNull()
    expect(quizErrorDetail(translate, null)).toBeNull()
  })
})
