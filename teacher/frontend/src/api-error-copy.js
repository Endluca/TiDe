const apiErrorCopy = {
  // Auth
  AUTH_REQUIRED: {
    en: "Your session has expired. Please sign in again.",
    zh: "登录状态已失效，请重新登录。",
  },
  INVALID_CREDENTIALS: {
    en: "The email or password is incorrect.",
    zh: "邮箱或密码错误。",
  },
  EMAIL_NOT_VERIFIED: {
    en: "Verify your email before signing in.",
    zh: "请先完成邮箱验证。",
  },
  TEACHER_NOT_FOUND: {
    en: "We could not find a teacher account for this teacher ID.",
    zh: "未找到可用于注册的教师编号。",
  },
  EMAIL_ALREADY_BOUND: {
    en: "This email is already linked to an account.",
    zh: "该邮箱已绑定账号。",
  },
  TEACHER_ALREADY_BOUND: {
    en: "This teacher ID is already linked to an account.",
    zh: "该教师编号已绑定账号。",
  },
  VERIFICATION_TOKEN_INVALID: {
    en: "This verification link is invalid or has expired. Request a new one.",
    zh: "验证链接无效或已过期，请重新发送。",
  },
  PASSWORD_RESET_TOKEN_INVALID: {
    en: "This password reset link is invalid or has expired. Request a new one.",
    zh: "重置链接无效或已过期，请重新申请。",
  },
  EMAIL_DOMAIN_NOT_ALLOWED: {
    en: "Use your company email address to register.",
    zh: "请使用公司邮箱注册。",
  },
  SOURCE_UNAVAILABLE: {
    en: "The required source data is temporarily unavailable. Please try again later.",
    zh: "所需来源数据暂时不可用，请稍后重试。",
  },

  // Tasks
  TASK_NOT_FOUND: {
    en: "This task could not be found.",
    zh: "未找到该任务。",
  },
  VALIDATION_NOT_FOUND: {
    en: "No validation result is available for this task yet.",
    zh: "该任务还没有提交验证结果。",
  },
  INVALID_IDEMPOTENCY_KEY: {
    en: "The request identifier is invalid. Please try again.",
    zh: "请求标识无效，请重试。",
  },
  IDEMPOTENCY_CONFLICT: {
    en: "This action conflicts with an earlier request. Refresh and try again.",
    zh: "本次操作与之前的请求冲突，请刷新后重试。",
  },
  STATE_VERSION_CONFLICT: {
    en: "The task has changed. Refresh it and try again.",
    zh: "任务状态已更新，请刷新后重试。",
  },
  INVALID_STATE_TRANSITION: {
    en: "This action is not available in the task's current status.",
    zh: "当前任务状态不允许该操作。",
  },
  STEP_NOT_FOUND: {
    en: "This task step could not be found.",
    zh: "任务步骤不存在。",
  },
  OUTPUT_INVALID: {
    en: "The submitted result does not match this task step.",
    zh: "提交结果与任务步骤不匹配。",
  },
  ATTEMPT_CONFLICT: {
    en: "This attempt has already been used. Refresh and try again.",
    zh: "本次尝试编号已被使用，请刷新后重试。",
  },
  CONTENT_NOT_READY: {
    en: "The official task content has not been published yet.",
    zh: "任务正式内容尚未发布。",
  },
  PREVIOUS_STEP_INCOMPLETE: {
    en: "Complete the previous step first.",
    zh: "请先完成前一个步骤。",
  },

  // Files
  IDEMPOTENCY_KEY_REUSED: {
    en: "This upload request has already been used for different content. Try again.",
    zh: "该上传请求已用于其他内容，请重新上传。",
  },
  UPLOAD_STEP_NOT_FOUND: {
    en: "The upload step could not be found.",
    zh: "未找到可上传文件的任务步骤。",
  },
  FILE_REQUIRED: {
    en: "Choose a file to upload.",
    zh: "请选择要上传的文件。",
  },
  FILE_METADATA_MISMATCH: {
    en: "The selected file does not match the upload request. Choose it again.",
    zh: "文件内容与上传请求不一致，请重新选择。",
  },
  FILE_SHA256_MISMATCH: {
    en: "The file verification value does not match. Upload the file again.",
    zh: "文件校验值不一致，请重新上传。",
  },
  FILE_CONTENT_NOT_UPLOADED: {
    en: "The file content has not finished uploading. Please try again.",
    zh: "文件内容尚未上传完成，请重试。",
  },
  FILE_CONTENT_UNAVAILABLE: {
    en: "The uploaded file is temporarily unavailable. Please try again later.",
    zh: "已上传的文件暂时不可用，请稍后重试。",
  },
  FILE_INTEGRITY_CHECK_FAILED: {
    en: "The file failed its integrity check. Upload it again.",
    zh: "文件完整性校验失败，请重新上传。",
  },
  FILE_NOT_READY: {
    en: "The file is still being verified. Please try again shortly.",
    zh: "文件尚未完成校验，请稍后重试。",
  },
  FILE_NOT_FOUND: {
    en: "This file could not be found.",
    zh: "未找到该文件。",
  },
  FILE_UPLOAD_NOT_PENDING: {
    en: "This file is no longer waiting to be uploaded.",
    zh: "该文件已不处于待上传状态。",
  },
  UPLOAD_INTENT_EXPIRED: {
    en: "The upload request has expired. Start the upload again.",
    zh: "上传请求已过期，请重新上传。",
  },
  FILE_TOO_LARGE: {
    en: "The file is too large. Choose a smaller file.",
    zh: "文件过大，请选择更小的文件。",
  },
  FILE_TYPE_NOT_ALLOWED: {
    en: "This file type is not supported.",
    zh: "不支持该文件类型。",
  },
  INVALID_FILENAME: {
    en: "The filename is invalid. Rename the file and try again.",
    zh: "文件名无效，请重命名后重试。",
  },

  // FAQ
  VALIDATION_FAILED: {
    en: "Check the information you entered and try again.",
    zh: "请检查填写内容后重试。",
  },
  FAQ_FEEDBACK_CONFLICT: {
    en: "Different feedback has already been submitted for this answer.",
    zh: "该回答已经提交过不同反馈。",
  },
  FAQ_RESOURCE_NOT_FOUND: {
    en: "This FAQ conversation or message could not be found.",
    zh: "问答会话或消息不存在。",
  },

  // Shared HTTP fallbacks
  INTERNAL_ERROR: {
    en: "The service is temporarily unavailable. Please try again later.",
    zh: "服务暂时不可用，请稍后重试。",
  },
  HTTP_400: {
    en: "The request could not be processed. Check the information and try again.",
    zh: "请求无法处理，请检查填写内容后重试。",
  },
  HTTP_401: {
    en: "Your session has expired. Please sign in again.",
    zh: "登录状态已失效，请重新登录。",
  },
  HTTP_403: {
    en: "You do not have permission to perform this action.",
    zh: "你暂时没有权限执行此操作。",
  },
  HTTP_404: {
    en: "The requested content could not be found.",
    zh: "未找到请求的内容。",
  },
  HTTP_409: {
    en: "The data has changed. Refresh and try again.",
    zh: "数据状态已变化，请刷新后重试。",
  },
  HTTP_413: {
    en: "The selected file is too large.",
    zh: "选择的文件过大。",
  },
  HTTP_415: {
    en: "This file type is not supported.",
    zh: "不支持该文件类型。",
  },
  HTTP_422: {
    en: "Check the submitted information and try again.",
    zh: "请检查提交内容后重试。",
  },
  HTTP_429: {
    en: "Requests are too frequent. Please try again shortly.",
    zh: "请求过于频繁，请稍后重试。",
  },
  HTTP_500: {
    en: "The service is temporarily unavailable. Please try again later.",
    zh: "服务暂时不可用，请稍后重试。",
  },
  HTTP_503: {
    en: "The service is temporarily unavailable. Please try again later.",
    zh: "服务暂时不可用，请稍后重试。",
  },
};

const validationCopy = {
  VALIDATION_RULES_NOT_CONFIGURED: {
    en: "This submission is waiting for manual review.",
    zh: "本次提交已进入人工复核。",
  },
  VALIDATION_HANDLER_NOT_AVAILABLE: {
    en: "This submission is waiting for manual review.",
    zh: "本次提交已进入人工复核。",
  },
  MANUAL_REVIEW_REQUIRED: {
    en: "This submission is waiting for manual review.",
    zh: "本次提交已进入人工复核。",
  },
  ALL_RULES_PASSED: {
    en: "All checks passed.",
    zh: "全部检查已通过。",
  },
  ALL_STEPS_COMPLETE: {
    en: "All required steps are complete.",
    zh: "全部必需步骤已完成。",
  },
  STEPS_INCOMPLETE: {
    en: "Complete the remaining steps before submitting again.",
    zh: "请完成剩余步骤后重新提交。",
  },
  IMAGE_REVIEW_CONFIG_INVALID: {
    en: "The automatic check is unavailable. This submission is waiting for manual review.",
    zh: "自动检查暂时不可用，本次提交已进入人工复核。",
  },
  IMAGE_REVIEW_FILE_MISSING: {
    en: "The submitted photo could not be found. Upload it again.",
    zh: "未找到已提交的照片，请重新上传。",
  },
  IMAGE_REVIEW_FILE_INVALID: {
    en: "The submitted photo cannot be checked. Upload a supported photo.",
    zh: "提交的照片无法检测，请上传支持的照片。",
  },
  IMAGE_REVIEW_FILE_UNAVAILABLE: {
    en: "The submitted photo is temporarily unavailable. This submission is waiting for manual review.",
    zh: "提交的照片暂时不可用，本次提交已进入人工复核。",
  },
  IMAGE_REVIEW_RESPONSE_INVALID: {
    en: "The automatic check did not return a reliable result. This submission is waiting for manual review.",
    zh: "自动检查未返回可靠结果，本次提交已进入人工复核。",
  },
  IMAGE_REVIEW_ERROR: {
    en: "The automatic check could not be completed. This submission is waiting for manual review.",
    zh: "自动检查暂时无法完成，本次提交已进入人工复核。",
  },
  IMAGE_REVIEW_PASSED: {
    en: "The photo meets the current requirements.",
    zh: "照片符合当前要求。",
  },
  IMAGE_REVIEW_RETRY: {
    en: "Adjust the photo based on the check results and submit it again.",
    zh: "请根据检查结果调整照片后重新提交。",
  },
  G01_EXTERNAL_STATUS_UNAVAILABLE: {
    en: "TESOL status is temporarily unavailable. Please try again later.",
    zh: "暂时无法读取 TESOL 状态，请稍后重试。",
  },
  G01_EXTERNAL_STATUS_PASSED: {
    en: "TESOL status has passed.",
    zh: "TESOL 状态已通过。",
  },
  G01_EXTERNAL_STATUS_INCOMPLETE: {
    en: "TESOL has not passed yet. You can still review the learning materials.",
    zh: "TESOL 尚未通过，你仍可查看相关学习资料。",
  },
};

const genericCopy = {
  en: "The action failed. Please try again.",
  zh: "操作失败，请重试。",
};

const automaticReviewCopy = {
  en: "The automatic check is temporarily unavailable. Please try again later.",
  zh: "自动检查暂时不可用，请稍后重试。",
};

const hasChinese = (value) => /[\u3400-\u9fff]/.test(value);
const languageKey = (language) => language === "zh" ? "zh" : "en";

function safeIncomingMessage(error, language) {
  const message = typeof error?.message === "string" ? error.message.trim() : "";
  if (!message) return "";
  return language === "zh"
    ? (hasChinese(message) ? message : "")
    : (hasChinese(message) ? "" : message);
}

export function localizeApiError(error, language, fallback = "") {
  const locale = languageKey(language);
  const code = typeof error?.code === "string" ? error.code : "";
  if (apiErrorCopy[code]) return apiErrorCopy[code][locale];
  if (fallback) return fallback;
  return safeIncomingMessage(error, locale) || genericCopy[locale];
}

export function localizedValidationMessage(validation, language, fallback = "") {
  const locale = languageKey(language);
  const code = typeof validation?.resultCode === "string" ? validation.resultCode : "";
  if (validationCopy[code]) return validationCopy[code][locale];
  if (code.startsWith("AI_GATEWAY_")) return automaticReviewCopy[locale];
  const incomingMessage = safeIncomingMessage(
    { message: validation?.teacherMessage },
    locale,
  );
  if (incomingMessage) return incomingMessage;
  if (fallback) return fallback;
  return genericCopy[locale];
}
