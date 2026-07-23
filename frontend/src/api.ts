import type {
  ApiErrorBody,
  AuditEvent,
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
  QueueItem,
  SharedTaskAssignment,
  TaskProgressAssignmentPage,
  TaskProgressResponse,
  TaskTemplate,
  TaskTemplateDefinition,
  Teacher,
  TeacherOption,
  TeacherPage,
} from './types'

export class ApiError extends Error {
  status: number
  body: ApiErrorBody

  constructor(status: number, body: ApiErrorBody) {
    super(body.error_code ?? `HTTP_${status}`)
    this.status = status
    this.body = body
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    credentials: 'include',
    headers: {
      'Content-Type': 'application/json',
      ...init?.headers,
    },
  })
  const body = (await response.json()) as T | ApiErrorBody
  if (!response.ok) {
    throw new ApiError(response.status, body as ApiErrorBody)
  }
  return body as T
}

export interface OutputFilters {
  type?: OutputType
  status?: OutputStatus
  teacher_id?: string
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
}

export interface TaskProgressAssignmentQuery {
  task_code: string
  title: string
  task_kind: string
  page?: number
  page_size?: number
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

export const api = {
  me: () => request<OperatorIdentity>('/api/auth/me'),
  logout: () => request<{ status: string }>('/api/auth/logout', { method: 'POST' }),
  health: () => request<{ status: string; mode: string }>('/api/health', { cache: 'no-store' }),
  dashboard: () => request<Dashboard>('/api/dashboard'),
  operationsOverview: () => request<OperationsOverview>('/api/operations/overview'),
  operationsInterventions: (filters: InterventionFilters = {}) => {
    const query = new URLSearchParams()
    if (filters.page !== undefined) query.set('page', String(filters.page))
    if (filters.page_size !== undefined) query.set('page_size', String(filters.page_size))
    if (filters.type) query.set('type', filters.type)
    if (filters.open_only !== undefined) query.set('open_only', String(filters.open_only))
    if (filters.status) query.set('status', filters.status)
    if (filters.domain) query.set('domain', filters.domain)
    if (filters.teacher_id) query.set('teacher_id', filters.teacher_id)
    const suffix = query.size ? `?${query.toString()}` : ''
    return request<OperationsInterventionResponse>(`/api/operations/interventions${suffix}`)
  },
  decideOperationsCase: (caseId: string, decision: 'START_PROCESSING' | 'RESOLVE', note: string) =>
    request<OperationsCaseDecisionResult>(`/api/operations/cases/${encodeURIComponent(caseId)}/decision`, {
      method: 'POST',
      body: JSON.stringify({ decision, note }),
    }),
  lessons: (filters: LessonEvidenceQuery = {}) => {
    const query = new URLSearchParams()
    if (filters.page !== undefined) query.set('page', String(filters.page))
    if (filters.page_size !== undefined) query.set('page_size', String(filters.page_size))
    if (filters.teacher_id) query.set('teacher_id', filters.teacher_id)
    if (filters.lesson_id) query.set('lesson_id', filters.lesson_id)
    if (filters.risk_only !== undefined) query.set('risk_only', String(filters.risk_only))
    const suffix = query.size ? `?${query.toString()}` : ''
    return request<LessonEvidencePage>(`/api/lessons${suffix}`)
  },
  teachers: (filters: TeacherListQuery = {}) => {
    const query = new URLSearchParams()
    if (filters.page !== undefined) query.set('page', String(filters.page))
    if (filters.page_size !== undefined) query.set('page_size', String(filters.page_size))
    if (filters.keyword) query.set('keyword', filters.keyword)
    if (filters.data_mode) query.set('data_mode', filters.data_mode)
    if (filters.employment_status) query.set('employment_status', filters.employment_status)
    const suffix = query.size ? `?${query.toString()}` : ''
    return request<TeacherPage>(`/api/teachers${suffix}`)
  },
  teacherOptions: () => request<TeacherOption[]>('/api/teacher-options'),
  teacher: (teacherId: string) => request<Teacher>(`/api/teachers/${teacherId}`),

  taskTemplates: () =>
    request<TaskTemplate[] | { items: TaskTemplate[]; total?: number }>('/api/task-templates'),
  taskAssignments: (filters: TaskAssignmentFilters = {}) => {
    const query = new URLSearchParams()
    if (filters.teacher_id) query.set('teacher_id', filters.teacher_id)
    if (filters.status) query.set('status', filters.status)
    if (filters.task_kind) query.set('task_kind', filters.task_kind)
    const suffix = query.size ? `?${query.toString()}` : ''
    return request<SharedTaskAssignment[]>(`/api/task-assignments${suffix}`)
  },
  taskProgress: () =>
    request<TaskProgressResponse>('/api/task-progress', { cache: 'no-store' }),
  taskProgressAssignments: (filters: TaskProgressAssignmentQuery) => {
    const query = new URLSearchParams({
      task_code: filters.task_code,
      title: filters.title,
      task_kind: filters.task_kind,
    })
    if (filters.page !== undefined) query.set('page', String(filters.page))
    if (filters.page_size !== undefined) query.set('page_size', String(filters.page_size))
    return request<TaskProgressAssignmentPage>(
      `/api/task-progress/assignments?${query.toString()}`,
      { cache: 'no-store' },
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

  queue: () => request<QueueItem[]>('/api/ops/action-queue'),
  events: () => request<AuditEvent[]>('/api/events'),
  outputs: (filters: OutputFilters = {}) => {
    const query = new URLSearchParams()
    if (filters.type) query.set('type', filters.type)
    if (filters.status) query.set('status', filters.status)
    if (filters.teacher_id) query.set('teacher_id', filters.teacher_id)
    const suffix = query.size ? `?${query.toString()}` : ''
    return request<OutputRecord[] | OutputListResponse>(`/api/outputs${suffix}`)
  },
  retryOutput: (outputId: string) =>
    request<OutputRecord>(`/api/outputs/${encodeURIComponent(outputId)}/retry`, { method: 'POST' }),
}
