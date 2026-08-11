import assert from "node:assert/strict";
import test from "node:test";
import {
  localizeApiError,
  localizedValidationMessage,
} from "../src/api-error-copy.js";

const stableApiCodes = {
  auth: [
    "AUTH_REQUIRED",
    "INVALID_CREDENTIALS",
    "EMAIL_NOT_VERIFIED",
    "TEACHER_NOT_FOUND",
    "EMAIL_ALREADY_BOUND",
    "TEACHER_ALREADY_BOUND",
    "VERIFICATION_TOKEN_INVALID",
    "PASSWORD_RESET_TOKEN_INVALID",
    "EMAIL_DOMAIN_NOT_ALLOWED",
    "SOURCE_UNAVAILABLE",
  ],
  task: [
    "TASK_NOT_FOUND",
    "VALIDATION_NOT_FOUND",
    "INVALID_IDEMPOTENCY_KEY",
    "IDEMPOTENCY_CONFLICT",
    "STATE_VERSION_CONFLICT",
    "INVALID_STATE_TRANSITION",
    "STEP_NOT_FOUND",
    "OUTPUT_INVALID",
    "ATTEMPT_CONFLICT",
    "FILE_NOT_READY",
    "CONTENT_NOT_READY",
    "PREVIOUS_STEP_INCOMPLETE",
  ],
  file: [
    "IDEMPOTENCY_KEY_REUSED",
    "UPLOAD_STEP_NOT_FOUND",
    "FILE_REQUIRED",
    "FILE_METADATA_MISMATCH",
    "FILE_SHA256_MISMATCH",
    "FILE_CONTENT_NOT_UPLOADED",
    "FILE_CONTENT_UNAVAILABLE",
    "FILE_INTEGRITY_CHECK_FAILED",
    "FILE_NOT_READY",
    "FILE_NOT_FOUND",
    "FILE_UPLOAD_NOT_PENDING",
    "UPLOAD_INTENT_EXPIRED",
    "FILE_TOO_LARGE",
    "FILE_TYPE_NOT_ALLOWED",
    "INVALID_FILENAME",
  ],
  faq: [
    "VALIDATION_FAILED",
    "IDEMPOTENCY_CONFLICT",
    "FAQ_FEEDBACK_CONFLICT",
    "FAQ_RESOURCE_NOT_FOUND",
  ],
};

for (const [area, codes] of Object.entries(stableApiCodes)) {
  test(`${area} stable API codes have Chinese and English copy`, () => {
    for (const code of codes) {
      const error = { code, message: "后端中文原文" };
      const english = localizeApiError(error, "en");
      const chinese = localizeApiError(error, "zh");
      assert.ok(english.length > 0, `${code} is missing English copy`);
      assert.doesNotMatch(english, /[\u3400-\u9fff]/, `${code} leaked Chinese into English`);
      assert.match(chinese, /[\u3400-\u9fff]/, `${code} is missing Chinese copy`);
    }
  });
}

test("unknown API errors never leak Chinese into the English interface", () => {
  assert.equal(
    localizeApiError({ code: "NEW_ERROR", message: "新的中文错误" }, "en"),
    "The action failed. Please try again.",
  );
  assert.equal(
    localizeApiError({ code: "NEW_ERROR", message: "A useful English error." }, "en"),
    "A useful English error.",
  );
  assert.equal(
    localizeApiError({ code: "NEW_ERROR", message: "新的中文错误" }, "en", "Unable to save."),
    "Unable to save.",
  );
});

const validationCodes = [
  "VALIDATION_RULES_NOT_CONFIGURED",
  "VALIDATION_HANDLER_NOT_AVAILABLE",
  "MANUAL_REVIEW_REQUIRED",
  "ALL_RULES_PASSED",
  "ALL_STEPS_COMPLETE",
  "STEPS_INCOMPLETE",
  "IMAGE_REVIEW_CONFIG_INVALID",
  "IMAGE_REVIEW_FILE_MISSING",
  "IMAGE_REVIEW_FILE_INVALID",
  "IMAGE_REVIEW_FILE_UNAVAILABLE",
  "IMAGE_REVIEW_RESPONSE_INVALID",
  "IMAGE_REVIEW_ERROR",
  "IMAGE_REVIEW_PASSED",
  "IMAGE_REVIEW_RETRY",
  "G01_EXTERNAL_STATUS_UNAVAILABLE",
  "G01_EXTERNAL_STATUS_PASSED",
  "G01_EXTERNAL_STATUS_INCOMPLETE",
];

test("stable task result codes have Chinese and English copy", () => {
  for (const resultCode of validationCodes) {
    const validation = { resultCode, teacherMessage: "后端中文教师提示" };
    const english = localizedValidationMessage(validation, "en");
    const chinese = localizedValidationMessage(validation, "zh");
    assert.doesNotMatch(english, /[\u3400-\u9fff]/, `${resultCode} leaked Chinese into English`);
    assert.match(chinese, /[\u3400-\u9fff]/, `${resultCode} is missing Chinese copy`);
  }
});

test("G01 validation copy refers only to TESOL status", () => {
  for (const resultCode of [
    "G01_EXTERNAL_STATUS_UNAVAILABLE",
    "G01_EXTERNAL_STATUS_PASSED",
    "G01_EXTERNAL_STATUS_INCOMPLETE",
  ]) {
    const validation = { resultCode, teacherMessage: "Self-intro 与 TESOL 状态" };
    assert.doesNotMatch(localizedValidationMessage(validation, "en"), /Self-intro/i);
    assert.doesNotMatch(localizedValidationMessage(validation, "zh"), /Self-intro/i);
    assert.match(localizedValidationMessage(validation, "en"), /TESOL/);
    assert.match(localizedValidationMessage(validation, "zh"), /TESOL/);
  }
});

test("AI gateway validation failures use localized automatic-review copy", () => {
  assert.equal(
    localizedValidationMessage(
      { resultCode: "AI_GATEWAY_UNAVAILABLE", teacherMessage: "人工复核" },
      "en",
    ),
    "The automatic check is temporarily unavailable. Please try again later.",
  );
});

test("unknown validation messages follow the active interface language", () => {
  assert.equal(
    localizedValidationMessage(
      { resultCode: "NEW_RESULT", teacherMessage: "后端中文教师提示" },
      "en",
      "Please review and try again.",
    ),
    "Please review and try again.",
  );
  assert.equal(
    localizedValidationMessage(
      { resultCode: "NEW_RESULT", teacherMessage: "后端中文教师提示" },
      "zh",
      "请检查后重试。",
    ),
    "后端中文教师提示",
  );
});
