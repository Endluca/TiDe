export type CompletionMethod =
  | 'QUIZ'
  | 'CHECKLIST'
  | 'UPLOAD_REVIEW'
  | 'DEVICE_CHECK'
  | 'EXTERNAL_SYNC'
  | 'CONFIRMATION_FORM'

export type VerificationMode =
  | 'SYSTEM_IMMEDIATE'
  | 'SYSTEM_ASYNC'
  | 'HUMAN_REVIEW'
  | 'BEHAVIOR_OBSERVATION'
  | 'HYBRID'

export type TemplateGroup =
  | 'ONBOARDING'
  | 'RELIABILITY'
  | 'DEVICE_NETWORK'
  | 'CLASSROOM_QUALITY'
  | 'USER_FEEDBACK'
  | 'CAPACITY'
  | 'STRUCTURED_CONFIRMATION'

export type TemplateStage =
  | 'ONBOARDING'
  | 'FIRST_CLASS_PREP'
  | 'TRIAL_EARLY'
  | 'TRIAL_MID'
  | 'PRE_GRADUATION'
  | 'EVENT_DRIVEN'

export type AutomationReadiness =
  | 'READY'
  | 'MOCK_READY'
  | 'DATA_PENDING'
  | 'OPS_CONFIRMED_ONLY'

export interface TemplateCloseCondition {
  mode: 'METHOD_VERIFIED' | 'EXTERNAL_SIGNAL_CONFIRMED' | 'OPS_DECISION_CONFIRMED'
  required_result: 'PASSED' | 'RECORDED' | 'OPS_CONFIRMED'
  required_signal_codes: string[]
  summary: string
}

export interface TemplateEffectObservation {
  enabled: boolean
  window_type: 'NONE' | 'NEXT_LESSONS' | 'NEXT_SAMPLES' | 'ROLLING_DAYS'
  target_count: number | null
  metric_codes: string[]
  success_summary: string | null
}

export interface TemplateScorePolicyReference {
  score_eligible: boolean
  score_bundle_id: string | null
  contribution_condition: string | null
}

export interface TemplateGovernance {
  ops_name_zh: string
  template_group: TemplateGroup
  stage_id: TemplateStage
  estimated_duration_minutes: number
  dependencies: string[]
  exclusive_with: string[]
  close_condition: TemplateCloseCondition
  effect_observation: TemplateEffectObservation
  manual_gate_summary: string | null
  score_policy: TemplateScorePolicyReference
  graduation_gate: 'NONE' | 'FIRST_CLASS_GATE' | 'CAMP_GRADUATION_GATE' | 'PROGRAM_ELIGIBILITY_GATE'
  automation_readiness: AutomationReadiness
  readiness_note: string
  sensitivity_level: 'NORMAL' | 'SENSITIVE' | 'HIGH_RISK'
  source_refs: string[]
}

export type OperatorRole =
  | 'VIEWER'
  | 'CASE_OPERATOR'
  | 'SENIOR_REVIEWER'
  | 'CONFIG_PUBLISHER'
  | 'EXTERNAL_ACTION_APPROVER'
  | 'AUDITOR'

export interface OperatorIdentity {
  operator_id: string
  username: string
  display_name?: string | null
  roles: OperatorRole[]
}

export type SourceMode =
  | 'REAL'
  | 'DERIVED_REAL'
  | 'MIXED'
  | 'MIXED_DERIVED'
  | 'MOCK'
  | 'MOCK_SIMULATION'
  | 'MOCK_PROXY'
  | 'SOURCE_MISSING'
  | 'MISSING_INPUT_ZERO'
  | 'SYSTEM_TASK_STATUS'
  | 'TASK_BASELINE_INCOMPLETE'
  | 'TASK_STATUS_PARTIAL'
  | 'TASK_STATUS_INVALID'
  | 'COMPLAINT_LEVEL_MAPPING_INCOMPLETE'
  | 'LEGACY_DIMENSION'
  | 'UNKNOWN'
export type TeacherDataMode = 'REAL' | 'MIXED' | 'MOCK' | 'UNKNOWN'

export interface Dimension {
  code: string
  label: string
  score: number
  source_mode?: SourceMode | string
  source_field?: string | null
  source_note?: string | null
  components?: DimensionComponent[]
}

export interface DimensionComponent {
  code: string
  metric: string
  value: unknown
  points_per_unit?: number
  maximum_points?: number
  achievement_rate?: number
  operator?: string
  threshold?: number
  milestone_achieved?: boolean
  settlement_mode?: string
  score: number
  source_mode?: SourceMode | string
}

export interface Signal {
  signal_id: string
  code: string
  status: string
  severity: string
  occurred_at: string
  route?: 'DIRECT' | 'AGENT' | 'MANUAL'
}

export interface Gate {
  code: string
  status: string
}

export interface HardGate {
  code: string
  metric?: string
  operator?: string
  threshold?: unknown
  actual?: unknown
  met?: boolean
  source_mode?: SourceMode | string
}

export interface HardGateGroup {
  met: boolean
  inherits_graduation?: boolean
  items: HardGate[]
}

export interface TeacherHardGates {
  graduation?: HardGateGroup
  gold?: HardGateGroup
}

export interface MetricProvenance {
  source_mode?: SourceMode | string
  source_field?: string | string[] | null
  batch_id?: string | null
  note?: string | null
  description?: string | null
}

export interface Teacher {
  teacher_id: string
  name: string
  avatar?: string
  country?: string | null
  timezone?: string | null
  camp_day?: number
  lessons_completed?: number
  total_score?: number
  raw_total_score?: number
  external_display_score?: number
  base_score?: number
  graduation_threshold?: number
  gold_threshold?: number
  graduation_external_score?: number
  gold_external_score?: number
  graduation_effect?: 'IMMEDIATE_ON_CRITERIA' | string
  graduation_score_threshold_met?: boolean
  graduation_criteria_met?: boolean
  gold_score_threshold_met?: boolean
  gold_criteria_met?: boolean
  graduation_state?: string
  data_mode?: TeacherDataMode | string
  employment_status?: string | null
  source_batch_id?: string | null
  source_snapshot_label?: string | null
  first_booked_date?: string | null
  is_cpl_tesol?: boolean | null
  is_self_introduce?: boolean | null
  score_policy_version?: string | null
  score_policy_sha256?: string | null
  score_projection_scope?: string | null
  source_snapshot_score_policy_version?: string | null
  source_snapshot_score_policy_sha256?: string | null
  score_policy_source?: string | null
  dimensions?: Dimension[]
  hard_gates?: TeacherHardGates | HardGate[] | Record<string, unknown>
  metric_inputs?: Record<string, unknown>
  metric_provenance?: Record<string, MetricProvenance | string | unknown>
  profile_provenance?: Record<string, MetricProvenance | string | unknown>
  gates?: Gate[]
  signals?: Signal[]
  risk_tags?: string[]
  next_best_action?: string
  updated_at?: string
  active_task_count?: number
  open_case_count?: number
  tasks?: Task[]
  task_assignments?: SharedTaskAssignment[]
  ops_cases?: OpsCase[]
  notifications?: Notification[]
  events?: AuditEvent[]
}

export interface TeacherOption {
  teacher_id: string
  name: string
  data_mode: TeacherDataMode | string
  employment_status: string | null
  graduation_state: string
  task_issuance_blockers: Array<'GRADUATED' | 'TIMEZONE_UNAVAILABLE' | string>
}

export interface TeacherListFilters {
  keyword: string
  data_mode: string | null
  employment_status: string | null
  available_data_modes: string[]
  available_employment_statuses: string[]
}

export interface TeacherPage {
  items: Teacher[]
  total: number
  page: number
  page_size: number
  total_pages: number
  filters: TeacherListFilters
}

export interface ConfirmationOption {
  option_code: string
  label_key: string
  public_consequence_key: string
}

export interface ActionSchema {
  action_schema_version: number
  completion_method: CompletionMethod
  submission_schema_version: number
  completion_behavior: string
  client_capability_requirements: string[]
  content_refs: Array<Record<string, unknown>>
  config: Record<string, unknown> & {
    question_id?: string
    question_version?: number
    options?: ConfirmationOption[]
    checklist_id?: string
    checklist_version?: number
    items?: Array<{ item_id: string; required: boolean }>
    attestation?: { required: boolean; version: number }
    quiz_id?: string
    quiz_version?: number
    metadata_fields?: Array<{
      field_id: string
      field_type: string
      required: boolean
      option_codes: string[]
    }>
    file_rules?: Array<{ purpose_code: string; required: boolean }>
    session_required?: boolean
  }
}

export interface Template {
  template_id: string
  template_version: number
  template_revision: number
  name: string
  localized_content: {
    title: string
    why: string
    how_to: string
    completion_standard: string
    result_copy: string
  }
  task_category: string
  dimension: string
  completion_method: CompletionMethod
  verification_mode?: VerificationMode
  priority: string
  due_hours: number
  status: 'DRAFT' | 'VALIDATED' | 'PUBLISHED' | 'RETIRED'
  trigger_code: string
  trigger_rule: TriggerRule
  public_reason_code: string
  action_schema: ActionSchema
  governance?: TemplateGovernance
  created_at?: string
  created_by?: string
  updated_at?: string
  updated_by?: string
  validated_at?: string | null
  published_at?: string | null
  retired_at?: string | null
}

export interface TriggerRule {
  signal_codes: string[]
  evidence: {
    accepted_statuses: Array<'CONFIRMED' | 'OPS_CONFIRMED'>
    minimum_reference_count: number
  }
  scope: {
    graduation_states: Array<'IN_PROGRESS'>
    countries: string[]
    minimum_camp_day: number
    maximum_camp_day: number
  }
  merge: {
    strategy: 'MERGE_INTO_ACTIVE_TASK' | 'NEW_TASK_PER_SIGNAL'
    evidence_window_hours: number
  }
  cooldown: {
    hours: number
    maximum_active_assignments: number
    allow_reissue_after_completion: boolean
  }
}

export type TaskTemplateStatus = 'DRAFT' | 'PUBLISHED' | 'RETIRED'
export type TaskTemplateScoreType = 'FIXED' | 'ZERO' | 'NOT_APPLICABLE'

/** Current task definition. The screen only exposes the fields needed by operators. */
export interface TaskTemplate {
  template_id: string
  revision: number
  status: TaskTemplateStatus
  output_type: 'TEACHER_TASK'
  audience: 'TEACHER'
  owner: string
  execution_owner: 'TEACHER_APP'
  integration_mode: 'OUTBOUND_MANAGED' | 'INBOUND_STATUS_ONLY'
  ops_name_zh: string
  content_locale: string
  category: string
  dimension: string
  stage: string
  title: string
  why_template: string
  how_summary: string
  completion_standard: string
  benefit: string
  help_ref: string | null
  priority: string
  due_rule: Record<string, unknown>
  appeal_mode: string
  external_task_template_code: string
  action_url: string | null
  score_type: TaskTemplateScoreType
  score_value: number
  source_mode: SourceMode | string
  source_refs: string[]
  created_at?: string
  updated_at?: string
  published_at?: string | null
  retired_at?: string | null
}

export type TaskTemplateDefinition = Omit<
  TaskTemplate,
  'template_id' | 'revision' | 'status' | 'created_at' | 'updated_at' | 'published_at' | 'retired_at'
>

/** Current task fact shared by the trigger center and teacher application. */
export interface SharedTaskAssignment {
  assignment_id: string
  teacher_id: string
  teacher_name?: string | null
  task_code: string
  task_kind: 'FIXED_GROWTH' | 'PERSONALIZED_IMPROVEMENT'
  creator_system: 'TEACHER_APP' | 'TRIGGER_CENTER'
  status: 'ASSIGNED' | 'VIEWED' | 'IN_PROGRESS' | 'SUBMITTED' | 'UNDER_REVIEW' | 'COMPLETED' | 'FAILED' | 'EXPIRED' | 'WAIVED' | 'CANCELLED'
  priority: 'P0' | 'P1' | 'P2' | 'P3'
  why: string
  title?: string | null
  what_to_do?: string | null
  completion_standard?: string | null
  outcome?: string | null
  due_at: string | null
  timezone_used: string | null
  timezone_source: string | null
  timezone_verified_at: string | null
  status_reason_code: string | null
  source_mode: 'REAL' | 'DERIVED_REAL' | 'MOCK' | 'MOCK_SIMULATION' | 'MOCK_PROXY'
  dedupe_key: string
  created_by: string
  updated_by: string
  row_version: number
  assigned_at: string
  status_changed_at: string
  completed_at: string | null
  created_at: string
  updated_at: string
}

export interface TaskProgressItem {
  task_code: string
  title: string
  task_kind: SharedTaskAssignment['task_kind']
  assigned_teacher_count: number
  assignment_count: number
  not_started: number
  in_progress: number
  completed: number
  other: number
  completion_rate: number
}

export interface TaskProgressResponse {
  items: TaskProgressItem[]
  total: number
}

export interface TaskProgressAssignmentPage {
  items: SharedTaskAssignment[]
  total: number
  page: number
  page_size: number
  total_pages: number
}

export interface TaskExecution {
  task_id: string
  runtime_status: string
  verification_result: string | null
  runtime_sequence: number
  due_status: string
  last_event_at: string
  selected_option_code: string | null
  result_ref: string | null
}

export interface OpsCase {
  case_id: string
  case_type: string
  teacher_id: string
  task_id: string
  priority: string
  status: string
  summary: string
  recommended_action: string
  external_action_status: string
  created_at: string
  decision: string | null
  decision_note?: string
  decided_at?: string
}

export interface Notification {
  notification_id: string
  task_id: string
  teacher_id: string
  template_id: string
  template_version: number
  channel: string
  priority: string
  safe_params: Record<string, unknown>
  deep_link: string
  locale: string
  expires_at: string | null
  status: 'REQUESTED' | 'STORED' | 'READ' | 'CLICKED' | 'INTEGRATION_FAILED' | 'CANCELLED'
  requested_at: string
  stored_at: string | null
  read_at: string | null
  clicked_at: string | null
  response_due_at: string | null
  failure_reason: string | null
  cancelled_at?: string | null
  cancellation_reason?: string | null
}

export interface Task {
  task_id: string
  obligation_id: string
  teacher_id: string
  template_id: string
  template_version: number
  template_revision_at_issue?: number
  name: string
  localized_content?: Template['localized_content']
  task_category: string
  dimension: string
  completion_method: CompletionMethod
  priority: string
  is_primary: boolean
  display_rank: number
  assignment_revision: number
  acknowledged_revision?: number | null
  execution_contract_version: number
  assignment_status: string
  slot_state: 'ROADMAP' | 'ACTIVE' | 'IN_REVIEW' | 'TERMINAL'
  decision_route?: 'DIRECT' | 'AGENT' | 'MANUAL'
  assigned_at: string | null
  acknowledged_at?: string
  runtime_task_ref?: string
  due_at: string | null
  public_reason: { code: string; params: Record<string, unknown> }
  trigger_rule_snapshot?: TriggerRule
  source_signal: Signal
  action_schema: ActionSchema
  allowed_actions: string[]
  execution: TaskExecution | null
  notification: Notification | null
  ops_cases: OpsCase[]
  events: AuditEvent[]
}

export interface QueueItem {
  queue_id: string
  queue_type: string
  priority: string
  teacher_id: string
  title: string
  summary: string
  status: string
  response_due_at?: string | null
  created_at: string
}

export interface AuditEvent {
  event_id: string
  event_type: string
  occurred_at: string
  teacher_id?: string
  task_id?: string
  runtime_event_code?: string
  runtime_sequence?: number
  runtime_status?: string
  verification_result?: string | null
  provider_event_id?: string | null
  payload?: Record<string, unknown>
}

export interface Dashboard {
  as_of: string
  graduation_threshold?: number
  gold_threshold?: number
  graduation_external_score?: number
  gold_external_score?: number
  graduation_effect?: 'IMMEDIATE_ON_CRITERIA' | string
  score_policy_version?: string
  score_policy_sha256?: string
  score_projection_scope?: string
  score_policy_source?: string
  teacher_count: number
  active_teacher_count: number
  settlement_pending_count: number
  issued_task_count: number
  active_shared_task_count?: number
  unacknowledged_task_count: number
  open_case_count: number
  completed_execution_count: number
  notification_integration_failure_count?: number
  p0_confirmation_waiting_count?: number
  real_teacher_count?: number
  mixed_teacher_count?: number
  mock_teacher_count?: number
  graduation_score_threshold_met_count?: number
  graduation_criteria_met_count?: number
  gold_score_threshold_met_count?: number
  gold_criteria_met_count?: number
  data_composition?: Partial<Record<TeacherDataMode | string, number>>
  data_mode_counts?: Record<string, number>
  employment_status_counts?: Record<string, number>
  funnel_by_employment_status?: Record<string, {
    teacher_count: number
    graduation_score_reached_count: number
    graduation_criteria_met_count: number
    gold_eligible_count: number
  }>
  graduation_score_reached_count?: number
  gold_score_reached_count?: number
  gold_eligible_count?: number
  dimension_averages: Array<{ code: string; label: string; average: number }>
}

export interface OperationsRiskBreakdown {
  domain: string
  label: string
  signal_count: number
  teacher_count: number
  open_output_count: number
}

export interface OperationsOverview {
  as_of: string | null
  teacher_total: number
  lesson_total: number
  affected_teacher_total: number
  current_ops_todo_count: number
  open_personalized_tasks: number
  severe_complaint_cases: number
  pending_data_issues: number
  risk_breakdown: OperationsRiskBreakdown[]
}

export interface OperationsIntervention {
  output_id: string
  output_type: string
  title: string
  teacher_id: string
  teacher_name: string
  domain: string
  priority: string
  status: string
  triggered_at: string
  why: string
  evidence_summary: string
  source_lesson_id: string | null
  signal_count: number
  source_lesson_ids: string[]
  action_label: string
}

export interface OperationsInterventionResponse {
  items: OperationsIntervention[]
  total: number
}

export interface OperationsCaseDecisionResult {
  case_id: string
  status: string
  decision_id: string
  updated_at: string
}

export interface AppNavigationContext {
  domain?: string
  teacherId?: string
  lessonId?: string
}

export interface LessonSignalEvidence {
  code?: string
  label?: string
  value?: unknown
  [key: string]: unknown
}

export interface LessonEvidence {
  lesson_id: string
  teacher_id: string
  teacher_name: string
  lesson_date: string | null
  lesson_time: string | null
  lesson_status: string
  risk_domains: string[]
  signals: Array<string | LessonSignalEvidence>
  complaint_level: string | null
}

export interface LessonEvidencePage {
  items: LessonEvidence[]
  total: number
  page: number
  page_size: number
}

export type OutputType =
  | 'TEACHER_TASK'
  | 'OPS_REVIEW_CASE'
  | 'SYSTEM_ACTION_REQUEST'
  | 'DELIVERY_INTENT'

export type OutputDisplayType =
  | 'TASK_ASSIGNMENT'
  | 'IN_APP_NOTIFICATION'
  | 'REMINDER'
  | 'OPS_CASE'
  | 'EXTERNAL_ACTION_REQUEST'
  | 'PROVIDER_REQUEST'

export type OutputAudience = 'TEACHER' | 'OPS' | 'EXTERNAL_SYSTEM'

export type OutputStatus =
  | 'PLANNED'
  | 'REQUESTED'
  | 'STORED'
  | 'DELIVERED'
  | 'READ'
  | 'CLICKED'
  | 'FAILED'
  | 'ACTION_PENDING'
  | 'CANCELLED'

export interface OutputRecord {
  output_id: string
  output_type?: OutputType | null
  display_type: OutputDisplayType
  delivery_kind?: string | null
  non_business?: boolean
  audience_type: OutputAudience
  recipient_id: string
  recipient_name: string
  channel: string
  source_type: string
  source_id: string
  teacher_id?: string | null
  task_id?: string | null
  case_id?: string | null
  status: OutputStatus
  title: string
  body?: string | null
  content?: string | null
  scheduled_at?: string | null
  created_at: string
  sent_at?: string | null
  delivered_at?: string | null
  attempt_count: number
  max_attempts: number
  next_retry_at?: string | null
  last_error?: string | Record<string, unknown> | null
  retryable: boolean
  requires_human_approval: boolean
  payload: Record<string, unknown>
}

export interface OutputListResponse {
  items: OutputRecord[]
  total: number
}

export interface OutputSummary {
  total: number
  by_type: Partial<Record<OutputType, number>>
  by_display_type?: Partial<Record<OutputDisplayType, number>>
  by_status: Partial<Record<OutputStatus, number>>
}

export interface ApiErrorBody {
  accepted?: false
  error_code?: string
  field_path?: string | null
  retryable?: boolean
  message_key?: string
  details?: Record<string, unknown>
  detail?: unknown
}

export interface AppSnapshot {
  dashboard: Dashboard | null
  /** Lightweight selector options; Teacher 360 owns its paged list request. */
  teachers: TeacherOption[]
  templates: Template[]
  tasks: Task[]
  cases: OpsCase[]
  queue: QueueItem[]
  notifications: Notification[]
  events: AuditEvent[]
}
