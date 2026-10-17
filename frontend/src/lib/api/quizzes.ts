import apiClient from './client'
import {
  CreateQuizRequest,
  Explanation,
  QuizPlay,
  QuizSummary,
  Submission,
  Verdict,
} from '@/lib/types/quiz'

export interface AttemptStarted {
  attempt_id: string
}

export const quizzesApi = {
  list: async (notebookId: string) => {
    const response = await apiClient.get<QuizSummary[]>(`/notebooks/${notebookId}/quizzes`)
    return response.data
  },

  create: async (notebookId: string, body: CreateQuizRequest) => {
    const response = await apiClient.post<QuizSummary>(`/notebooks/${notebookId}/quizzes`, body)
    return response.data
  },

  get: async (quizId: string, attemptId: string | null) => {
    const response = await apiClient.get<QuizPlay>(`/quizzes/${quizId}`, {
      params: attemptId ? { attempt_id: attemptId } : undefined,
    })
    return response.data
  },

  start: async (quizId: string) => {
    const response = await apiClient.post<AttemptStarted>(`/quizzes/${quizId}/attempts/start`)
    return response.data
  },

  retry: async (quizId: string) => {
    const response = await apiClient.post<QuizSummary>(`/quizzes/${quizId}/retry`)
    return response.data
  },

  commit: async (
    quizId: string,
    body: { attempt_id: string; question_id: string; submission: Submission }
  ) => {
    const response = await apiClient.post<Verdict>(`/quizzes/${quizId}/attempts`, body)
    return response.data
  },

  explain: async (quizId: string, questionId: string, attemptId: string) => {
    const response = await apiClient.post<Explanation>(
      `/quizzes/${quizId}/questions/${questionId}/explain`,
      { attempt_id: attemptId }
    )
    return response.data
  },
}
