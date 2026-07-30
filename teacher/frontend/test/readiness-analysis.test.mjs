import assert from "node:assert/strict";
import test from "node:test";
import {
  isReadinessPendingStatus,
  normalizeReadinessAnalysis,
} from "../src/features/task-content/readiness-analysis.js";

const criteria = [
  { id: "camera_angle", title: "摄像头角度", detail: "调整角度" },
  { id: "lighting", title: "光线", detail: "调整光线" },
  { id: "background", title: "背景", detail: "整理背景" },
  { id: "dressing", title: "着装", detail: "调整着装" },
];
const c = (_en, zh) => zh;
const englishCriteria = [
  { id: "camera_angle", title: "Camera angle", detail: "Adjust the camera angle." },
  { id: "lighting", title: "Lighting", detail: "Adjust the lighting." },
  { id: "background", title: "Background", detail: "Tidy the background." },
  { id: "dressing", title: "Dressing", detail: "Adjust the visible clothing." },
];
const en = (english) => english;

test("pending backend records stay processing instead of becoming four uncertain checks", () => {
  const result = normalizeReadinessAnalysis(
    { status: "CHECKING", checks: [] },
    criteria,
    c,
  );

  assert.equal(result.status, "processing");
  assert.deepEqual(result.checks, []);
  assert.equal(isReadinessPendingStatus("CHECKING"), true);
  assert.equal(result.teacherMessage, "正在审核，预计 10-15 秒。");
});

test("technical errors are shown as unavailable without judging the teacher", () => {
  const result = normalizeReadinessAnalysis(
    {
      status: "UNDER_REVIEW",
      decision: "ERROR",
      checks: [],
      teacherMessage: "自动检测服务暂时不可用",
    },
    criteria,
    c,
  );

  assert.equal(result.status, "unavailable");
  assert.deepEqual(result.checks, []);
  assert.equal(result.teacherMessage, "自动检测服务暂时不可用");
});

test("complete retry results still show the four criterion outcomes", () => {
  const result = normalizeReadinessAnalysis(
    {
      status: "RETRY_REQUIRED",
      decision: "RETRY",
      checks: criteria.map((criterion, index) => ({
        id: criterion.id,
        status: index === 0 ? "fail" : "pass",
        message: index === 0 ? "请调整" : "符合要求",
      })),
    },
    criteria,
    c,
  );

  assert.equal(result.status, "changes_requested");
  assert.equal(result.checks.length, 4);
  assert.equal(result.checks[0].status, "fail");
});

test("English results use stable frontend copy instead of backend Chinese", () => {
  const result = normalizeReadinessAnalysis(
    {
      status: "RETRY_REQUIRED",
      decision: "RETRY",
      teacherMessage: "请调整后重新拍照",
      checks: englishCriteria.map((criterion, index) => ({
        id: criterion.id,
        status: index === 0 ? "fail" : "pass",
        message: index === 0 ? "摄像头角度需要调整" : "符合当前要求",
        suggestion: index === 0 ? "请把镜头调整到眼睛高度" : "",
      })),
    },
    englishCriteria,
    en,
  );

  assert.equal(
    result.teacherMessage,
    "Some items need adjustment. Review the results, then retake the photo.",
  );
  assert.equal(result.checks[0].message, "The camera angle needs adjustment.");
  assert.equal(result.checks[0].suggestion, "Adjust the camera angle.");
  assert.equal(
    result.checks.some((check) =>
      /[\u3400-\u9fff]/u.test(`${check.title}${check.message}${check.suggestion}`),
    ),
    false,
  );
});

test("English teacher messages follow backend processing and error statuses", () => {
  const beautifying = normalizeReadinessAnalysis(
    {
      status: "BEAUTIFYING",
      decision: "PASS",
      teacherMessage: "正在处理并保存照片",
    },
    englishCriteria,
    en,
  );
  const failed = normalizeReadinessAnalysis(
    {
      status: "PROCESSING_FAILED",
      decision: "ERROR",
      teacherMessage: "自动检测服务暂时不可用",
    },
    englishCriteria,
    en,
  );

  assert.equal(
    beautifying.teacherMessage,
    "The check passed. The photo is being prepared and saved.",
  );
  assert.equal(
    failed.teacherMessage,
    "The automatic camera check could not be completed. Please try again.",
  );
});

test("Chinese results may keep backend teacher and criterion copy", () => {
  const result = normalizeReadinessAnalysis(
    {
      status: "RETRY_REQUIRED",
      decision: "RETRY",
      teacherMessage: "请根据检测结果调整",
      checks: criteria.map((criterion, index) => ({
        id: criterion.id,
        status: index === 0 ? "fail" : "pass",
        message: index === 0 ? "镜头位置偏低" : "符合要求",
        suggestion: index === 0 ? "请将镜头抬高" : "",
      })),
    },
    criteria,
    c,
  );

  assert.equal(result.teacherMessage, "请根据检测结果调整");
  assert.equal(result.checks[0].message, "镜头位置偏低");
  assert.equal(result.checks[0].suggestion, "请将镜头抬高");
});
