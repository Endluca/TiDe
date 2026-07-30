#!/usr/bin/env ruby
# frozen_string_literal: true

require 'json'
require 'yaml'

root = File.expand_path(__dir__)
openapi = YAML.load_file(File.join(root, 'openapi.yaml'))
paths = openapi.fetch('paths')
schemas = openapi.fetch('components').fetch('schemas')

raise 'OpenAPI 版本必须为 3.1.0' unless openapi['openapi'] == '3.1.0'
raise '公共路径数量不足' unless paths.size >= 29
raise '旧世文 HTTP 入站路径仍存在' if paths.key?('/integrations/shiwen/v2/assignments')
raise '已移除的 Support 路径仍存在' if paths.key?('/api/v1/support/requests')
raise '已移除的 client-events 路径仍存在' if paths.key?('/api/v1/client-events')
raise 'app-events 路径缺失' unless paths.key?('/api/v1/app-events')
raise '任务显式已查看路径缺失' unless paths.key?('/api/v1/tasks/{taskInstanceId}/view')
raise '消息点击回写路径缺失' unless paths.key?('/api/v1/me/notifications/{sourceNotificationId}/click')

refs = []
walk = lambda do |value|
  case value
  when Hash
    value.each do |key, child|
      refs << child if key == '$ref' && child.start_with?('#/')
      walk.call(child)
    end
  when Array
    value.each { |child| walk.call(child) }
  end
end
walk.call(openapi)

refs.uniq.each do |ref|
  node = openapi
  ref.delete_prefix('#/').split('/').each do |part|
    node = node.fetch(part.gsub('~1', '/').gsub('~0', '~'))
  end
end

expected_statuses = %w[ASSIGNED VIEWED IN_PROGRESS SUBMITTED UNDER_REVIEW COMPLETED FAILED EXPIRED WAIVED CANCELLED]
raise '任务状态契约未对齐共享表' unless schemas.fetch('TaskStatus').fetch('enum') == expected_statuses
raise '任务列表仍含旧 assignmentSync' if schemas.fetch('TaskListResponse').fetch('properties').key?('assignmentSync')
raise '共享 assignment 不应允许 null' unless schemas.fetch('TaskContext').fetch('properties').fetch('assignment').fetch('$ref') == '#/components/schemas/SharedAssignmentContext'

fixed = JSON.parse(File.read(File.join(root, 'examples/fixed-task-context.mock.json')))
raise '固定任务必须关联共享 assignment' unless fixed['kind'] == 'FIXED_GROWTH' && fixed['assignment']
raise '示例状态未对齐共享表' unless fixed['status'] == 'ASSIGNED'
raise '示例必须显式标为 Mock' unless fixed['dataOrigin'] == 'MOCK'
raise '示例 assignmentId 必须与 taskInstanceId 相同' unless fixed.dig('assignment', 'assignmentId') == fixed['taskInstanceId']

type_contract = File.read(File.join(root, 'task-contract.ts'))
%w[RegisterRequest RegistrationAccepted EmailVerificationResponse LoginRequest AuthTokenPair PasswordResetCompleted TeacherProfile G01Review Notification NotificationListResponse TaskSummary TaskListResponse TaskContext SharedAssignmentContext MutationMeta ViewTaskRequest UploadIntentRequest UploadIntentResponse CompleteUploadRequest FileObject SourceFreshness FaqConversationCreated AskFaqRequest FaqMessage FaqConversation FaqAnswerResponse FaqFeedbackRequest FaqFeedbackResponse CreateAppEvent AppEventAccepted ApiError].each do |name|
  raise "TypeScript 合同缺少 #{name}" unless type_contract.include?("interface #{name}")
end

%w[assignmentSync PersonalizedAssignmentContext CreateSupportRequest SupportRequest CreateClientEvent ClientEventAccepted].each do |name|
  raise "TypeScript 合同仍包含旧类型 #{name}" if type_contract.include?(name)
end

puts "OpenAPI: #{paths.size} paths"
puts "Internal refs: #{refs.uniq.size} resolved"
puts 'Shared fixed-task Mock example: valid'
puts 'TypeScript public contract: shared-database types valid'
