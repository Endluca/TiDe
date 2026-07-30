import type {
  ApiErrorBody,
  AuditEventPage,
  Dashboard,
  OperatorIdentity,
  LessonEvidencePage,
  OperationsInterventionResponse,
  OperationsCaseDecisionResult,
  OperationsOverview,
  OutputListResponse,
  OutputRecord,
  OutputStatus,
  OutputType,
  SharedTaskAssignment,
  SharedTaskAssignmentPage,
  SupportTicket,
  SupportTicketPage,
  SupportTicketSummary,
  SupportTicketWorkflowState,
  TaskProgressAssignmentPage,
  TaskProgressResponse,
  TaskTemplate,
  TaskTemplateDefinition,
  Teacher,
  TeacherOption,
  TeacherPage,
} from './types'

const DEFAULT_GET_TIMEOUT_MS = 20_000
const DEFAULT_WRITE_TIMEOUT_MS = 60_000

export class ApiError extends Error {
  status: number
  body: ApiErrorBody

  constructor(status: number, body: ApiErrorBody) {
    super(body.error_code ?? `HTTP_${status}`)
    this.status = status
    this.body = body
  }
}

export class RequestCancelledError extends Error {
  constructor(message = 'Request cancelled') {
    super(message)
    this.name = 'RequestCancelledError'
  }
}

export class RequestTimeoutError extends Error {
  timeoutMs: number

  constructor(timeoutMs: number) {
    super(`Request timed out after ${timeoutMs}ms`)
    this.name = 'RequestTimeoutError'
    this.timeoutMs = timeoutMs
  }
}

export interface RequestOptions {
  signal?: AbortSignal
  timeoutMs?: number
}

export interface LatestRequestController {
  run<T>(operation: (options: RequestOptions) => Promise<T>): Promise<T>
  cancel(): void
}

export function createLatestRequestController(): LatestRequestController {
  let sequence = 0
  let activeController: AbortController | undefined

  return {
    async run<T>(operation: (options: RequestOptions) => Promise<T>): Promise<T> {
      activeController?.abort()
      const controller = new AbortController()
      const currentSequence = ++sequence
      activeController = controller
      try {
        const result = await operation({ signal: controller.signal })
        if (currentSequence !== sequence) throw new RequestCancelledError('Stale request result')
        return result
      } finally {
        if (currentSequence === sequence) activeController = undefined
      }
    },
    cancel() {
      sequence += 1
      activeController?.abort()
      activeController = undefined
    },
  }
}

export function isRequestCancelled(error: unknown): boolean {
  return error instanceof RequestCancelledError
    || (error instanceof DOMException && error.name === 'AbortError')
}

function errorBodyMessage(body: ApiErrorBody, fallback: string): string {
  if (typeof body.detail === 'string') return body.detail
  if (body.detail && typeof body.detail === 'object') {
    const detail = body.detail as Record<string, unknown>
    if (typeof detail.message === 'string') return detail.message
  }
  return fallback
}

export function apiErrorMessage(error: unknown, fallback: string): string {
  if (!(error instanceof ApiError)) return error instanceof Error ? error.message : fallback
  return errorBodyMessage(error.body, fallback)
}

export async function request<T>(
  path: string,
  init: RequestInit = {},
  options: RequestOptions = {},
): Promise<T> {
  const method = (init.method ?? 'GET').toUpperCase()
  const timeoutMs = options.timeoutMs
    ?? (method === 'GET' || method === 'HEAD' ? DEFAULT_GET_TIMEOUT_MS : DEFAULT_WRITE_TIMEOUT_MS)
  const controller = new AbortController()
  let callerAborted = options.signal?.aborted ?? false
  let timedOut = false
  const abortFromCaller = () => {
    callerAborted = true
    controller.abort()
  }
  options.signal?.addEventListener('abort', abortFromCaller, { once: true })
  if (callerAborted) controller.abort()
  const timeout = globalThis.setTimeout(() => {
    timedOut = true
    controller.abort()
  }, timeoutMs)

  try {
    const response = await fetch(path, {
      ...init,
      signal: controller.signal,
      credentials: 'include',
      headers: {
        'Content-Type': 'application/json',
        ...init.headers,
      },
    })
    let body: T | ApiErrorBody
    try {
      body = (await response.json()) as T | ApiErrorBody
    } catch {
      if (timedOut) throw new RequestTimeoutError(timeoutMs)
      if (callerAborted) throw new RequestCancelledError()
      throw new ApiError(response.status, {
        error_code: response.ok ? 'INVALID_JSON_RESPONSE' : `HTTP_${response.status}`,
        detail: response.statusText || 'The service returned a non-JSON response.',
      })
    }
    if (timedOut) throw new RequestTimeoutError(timeoutMs)
    if (callerAborted) throw new RequestCancelledError()
    if (!response.ok) {
      throw new ApiError(response.status, body as ApiErrorBody)
    }
    return body as T
  } catch (error) {
    if (timedOut) throw new RequestTimeoutError(timeoutMs)
    if (callerAborted || (error instanceof DOMException && error.name === 'AbortError')) {
      throw new RequestCancelledError()
    }
    throw error
  } finally {
    globalThis.clearTimeout(timeout)
    options.signal?.removeEventListener('abort', abortFromCaller)
  }
}

export interface OutputFilters {
  type?: OutputType | string
  status?: OutputStatus
  teacher_id?: string
  keyword?: string
  operational_only?: boolean
  include_task_assignments?: boolean
  page?: number
  page_size?: number
}

export interface TeacherListQuery {
  page?: number
  page_size?: number
  keyword?: string
  data_mode?: string
  employment_status?: string
}

export interface TaskAssignmentFilters {
  teacher_id?: string
  status?: string
  task_kind?: string
  include_mock?: boolean
  page?: number
  page_size?: number
}

export interface TaskProgressAssignmentQuery {
  task_code: string
  title: string
  task_kind: string
  keyword?: string
  page?: number
  page_size?: number
}

export interface TaskProgressQuery {
  keyword?: string
}

export interface InterventionFilters {
  page?: number
  page_size?: number
  type?: string
  open_only?: boolean
  status?: string
  domain?: string
  teacher_id?: string
}

export interface LessonEvidenceQuery {
  page?: number
  page_size?: number
  teacher_id?: string
  lesson_id?: string
  risk_only?: boolean
}

export interface SupportTicketQuery {
  workflow_state?: SupportTicketWorkflowState
  secondary_category?: string
  keyword?: string
  page?: number
  page_size?: number
}

export interface TeacherOptionQuery {
  keyword?: string
  limit?: number
}

export const api = {
  login: (body: { username: string; password: string }) =>
    request<OperatorIdentity>('/api/auth/login', {
      method: 'POST',
      body: JSON.stringify(body),
    }),
  me: (options?: RequestOptions) => request<OperatorIdentity>('/api/auth/me', {}, options),
  logout: () => request<{ status: string }>('/api/auth/logout', { method: 'POST' }),
  health: (options?: RequestOptions) => request<{ status: string; mode: string }>('/api/health', { cache: 'no-store' }, options),
  dashboard: (options?: RequestOptions) => request<Dashboard>('/api/dashboard', {}, options),
  operationsOverview: (options?: RequestOptions) => request<OperationsOverview>('/api/operations/overview', {}, options),
  operationsInterventions: (filters: InterventionFilters = {}, options?: RequestOptions) => {
    const query = new URLSearchParams()
    if (filters.page !== undefined) query.set('page', String(filters.page))
    if (filters.page_size !== undefined) query.set('page_size', String(filters.page_size))
    if (filters.type) query.set('type', filters.type)
    if (filters.open_only !== undefined) query.set('open_only', String(filters.open_only))
    if (filters.status) query.set('status', filters.status)
    if (filters.domain) query.set('domain', filters.domain)
    if (filters.teacher_id) query.set('teacher_id', filters.teacher_id)
    const suffix = query.size ? `?${query.toString()}` : ''
    return request<OperationsInterventionResponse>(`/api/operations/interventions${suffix}`, {}, options)
  },
  decideOperationsCase: (caseId: string, decision: 'START_PROCESSING' | 'RESOLVE', note: string) =>
    request<OperationsCaseDecisionResult>(`/api/operations/cases/${encodeURIComponent(caseId)}/decision`, {
      method: 'POST',
      body: JSON.stringify({ decision, note }),
    }),
  lessons: (filters: LessonEvidenceQuery = {}, options?: RequestOptions) => {
    const query = new URLSearchParams()
    if (filters.page !== undefined) query.set('page', String(filters.page))
    if (filters.page_size !== undefined) query.set('page_size', String(filters.page_size))
    if (filters.teacher_id) query.set('teacher_id', filters.teacher_id)
    if (filters.lesson_id) query.set('lesson_id', filters.lesson_id)
    if (filters.risk_only !== undefined) query.set('risk_only', String(filters.risk_only))
    const suffix = query.size ? `?${query.toString()}` : ''
    return request<LessonEvidencePage>(`/api/lessons${suffix}`, {}, options)
  },
  teachers: (filters: TeacherListQuery = {}, options?: RequestOptions) => {
    const query = new URLSearchParams()
    if (filters.page !== undefined) query.set('page', String(filters.page))
    if (filters.page_size !== undefined) query.set('page_size', String(filters.page_size))
    if (filters.keyword) query.set('keyword', filters.keyword)
    if (filters.data_mode) query.set('data_mode', filters.data_mode)
    if (filters.employment_status) query.set('employment_status', filters.employment_status)
    const suffix = query.size ? `?${query.toString()}` : ''
    return request<TeacherPage>(`/api/teachers${suffix}`, {}, options)
  },
  teacherOptions: (filters: TeacherOptionQuery = {}, options?: RequestOptions) => {
    const query = new URLSearchParams()
    if (filters.keyword) query.set('keyword', filters.keyword)
    if (filters.limit !== undefined) query.set('limit', String(filters.limit))
    const suffix = query.size ? `?${query.toString()}` : ''
    return request<TeacherOption[]>(`/api/teacher-options${suffix}`, {}, options)
  },
  teacher: (teacherId: string, options?: RequestOptions) =>
    request<Teacher>(`/api/teachers/${teacherId}`, {}, options),
  supportTicketSummary: (options?: RequestOptions) =>
    request<SupportTicketSummary>('/api/support-tickets/summary', { cache: 'no-store' }, options),
  supportTickets: (filters: SupportTicketQuery = {}, options?: RequestOptions) => {
    const query = new URLSearchParams()
    if (filters.workflow_state) query.set('workflow_state', filters.workflow_state)
    if (filters.secondary_category) query.set('secondary_category', filters.secondary_category)
    if (filters.keyword) query.set('keyword', filters.keyword)
    if (filters.page !== undefined) query.set('page', String(filters.page))
    if (filters.page_size !== undefined) query.set('page_size', String(filters.page_size))
    const suffix = query.size ? `?${query.toString()}` : ''
    return request<SupportTicketPage>(`/api/support-tickets${suffix}`, { cache: 'no-store' }, options)
  },
  supportTicket: (ticketId: string, options?: RequestOptions) =>
    request<SupportTicket>(`/api/support-tickets/${encodeURIComponent(ticketId)}`, { cache: 'no-store' }, options),
  replySupportTicket: (
    ticketId: string,
    body: { message_id: string; expected_row_version: number; content: string },
  ) => request<SupportTicket>(`/api/support-tickets/${encodeURIComponent(ticketId)}/operator-replies`, {
    method: 'POST',
    body: JSON.stringify(body),
  }),

  taskTemplates: (options?: RequestOptions) =>
    request<TaskTemplate[] | { items: TaskTemplate[]; total?: number }>('/api/task-templates', {}, options),
  taskAssignments: (filters: TaskAssignmentFilters = {}, options?: RequestOptions) => {
    const query = new URLSearchParams()
    if (filters.teacher_id) query.set('teacher_id', filters.teacher_id)
    if (filters.status) query.set('status', filters.status)
    if (filters.task_kind) query.set('task_kind', filters.task_kind)
    if (filters.include_mock !== undefined) query.set('include_mock', String(filters.include_mock))
    if (filters.page !== undefined) query.set('page', String(filters.page))
    if (filters.page_size !== undefined) query.set('page_size', String(filters.page_size))
    const suffix = query.size ? `?${query.toString()}` : ''
    return request<SharedTaskAssignmentPage>(`/api/task-assignments${suffix}`, {}, options)
  },
  taskProgress: (filters: TaskProgressQuery = {}, options?: RequestOptions) => {
    const query = new URLSearchParams()
    if (filters.keyword) query.set('keyword', filters.keyword)
    const suffix = query.size ? `?${query.toString()}` : ''
    return request<TaskProgressResponse>(
      `/api/task-progress${suffix}`,
      { cache: 'no-store' },
      options,
    )
  },
  taskProgressAssignments: (filters: TaskProgressAssignmentQuery, options?: RequestOptions) => {
    const query = new URLSearchParams({
      task_code: filters.task_code,
      title: filters.title,
      task_kind: filters.task_kind,
    })
    if (filters.keyword) query.set('keyword', filters.keyword)
    if (filters.page !== undefined) query.set('page', String(filters.page))
    if (filters.page_size !== undefined) query.set('page_size', String(filters.page_size))
    return request<TaskProgressAssignmentPage>(
      `/api/task-progress/assignments?${query.toString()}`,
      { cache: 'no-store' },
      options,
    )
  },
  createTaskTemplate: (body: TaskTemplateDefinition & { template_id: string; idempotency_key: string }) =>
    request<TaskTemplate>('/api/task-templates', { method: 'POST', body: JSON.stringify(body) }),
  updateTaskTemplate: (template: TaskTemplate, definition: TaskTemplateDefinition) =>
    request<TaskTemplate>(`/api/task-templates/${encodeURIComponent(template.template_id)}`, {
      method: 'PUT',
      body: JSON.stringify({ expected_revision: template.revision, ...definition }),
    }),
  publishTaskTemplate: (template: TaskTemplate) =>
    request<TaskTemplate>(`/api/task-templates/${encodeURIComponent(template.template_id)}/publish`, {
      method: 'POST',
      body: JSON.stringify({ expected_revision: template.revision }),
    }),

  events: (filters: {
    page?: number
    page_size?: number
    teacher_id?: string
    keyword?: string
  } = {}, options?: RequestOptions) => {
    const query = new URLSearchParams()
    if (filters.page !== undefined) query.set('page', String(filters.page))
    if (filters.page_size !== undefined) query.set('page_size', String(filters.page_size))
    if (filters.teacher_id) query.set('teacher_id', filters.teacher_id)
    if (filters.keyword) query.set('keyword', filters.keyword)
    const suffix = query.size ? `?${query.toString()}` : ''
    return request<AuditEventPage>(`/api/events${suffix}`, { cache: 'no-store' }, options)
  },
  outputs: (filters: OutputFilters = {}, options?: RequestOptions) => {
    const query = new URLSearchParams()
    if (filters.type) query.set('type', filters.type)
    if (filters.status) query.set('status', filters.status)
    if (filters.teacher_id) query.set('teacher_id', filters.teacher_id)
    if (filters.keyword) query.set('keyword', filters.keyword)
    if (filters.operational_only !== undefined) query.set('operational_only', String(filters.operational_only))
    if (filters.include_task_assignments !== undefined) query.set('include_task_assignments', String(filters.include_task_assignments))
    if (filters.page !== undefined) query.set('page', String(filters.page))
    if (filters.page_size !== undefined) query.set('page_size', String(filters.page_size))
    const suffix = query.size ? `?${query.toString()}` : ''
    return request<OutputListResponse>(`/api/outputs${suffix}`, {}, options)
  },
  retryOutput: (outputId: string) =>
    request<OutputRecord>(`/api/outputs/${encodeURIComponent(outputId)}/retry`, { method: 'POST' }),
}
