import assert from "node:assert/strict";
import test from "node:test";
import {
  courseParticipationKey,
  courseScoreSourceLabels,
  courseSourcesForDimension,
  lessonLifecycleStatusLabel,
  mergeScorecardCourseSources,
  positiveCourseScoreSources,
  visibleCourseIndicators,
} from "../src/course-score-sources.js";

test("maps the hardware-quality component to teacher-facing copy", () => {
  assert.deepEqual(courseScoreSourceLabels.CLASS_QUALITY_HARDWARE, {
    en: "No device, network, or teaching-environment issues",
    zh: "无设备网络&教学环境问题",
  });
});

const course = {
  lessonId: "participation:v1:compat-display-only",
  sourceRegion: "dom",
  sourceAppointId: "appoint-001",
  participationSeq: 1,
  lessonSequence: 3,
  scheduledStartAt: "2026-07-21T08:00:00.000Z",
  lifecycleStatus: "end",
  scoreRuleVersion: "score-rule-v3",
  facts: {
    late: false,
    earlyLeave: false,
    positiveFeedback: true,
    favorited: false,
    rebooked: false,
    cameraOff: false,
    cpuUsageHigh: false,
    networkDelayHigh: false,
  },
  dimensions: [
    {
      code: "USER_FEEDBACK",
      components: [
      {
        code: "FEEDBACK_PRAISE",
        score: 5,
        awarded: true,
        evidenceStatus: "READY",
      },
      ],
    },
    {
      code: "RELIABILITY",
      components: [
      {
        code: "ON_TIME_COMPLETED",
        score: 2,
        awarded: true,
        evidenceStatus: "READY",
      },
      ],
    },
    {
      code: "CLASS_QUALITY",
      components: [
      {
        code: "CLASS_QUALITY_PERFECT_COUNT",
        score: 0,
        awarded: false,
        evidenceStatus: "SOURCE_MISSING",
      },
      ],
    },
  ],
};

test("keeps positive course score sources only", () => {
  assert.deepEqual(positiveCourseScoreSources(course), [
    {
      dimension: "USER_FEEDBACK",
      sourceKey: "FEEDBACK_PRAISE",
      score: 5,
      scoreRuleVersion: "score-rule-v3",
      evidenceStatus: "READY",
    },
    {
      dimension: "RELIABILITY",
      sourceKey: "ON_TIME_COMPLETED",
      score: 2,
      scoreRuleVersion: "score-rule-v3",
      evidenceStatus: "READY",
    },
  ]);
});

test("groups course sources into the matching cumulative dimension", () => {
  assert.deepEqual(courseSourcesForDimension([course], "userFeedback"), [
    {
      sourceKey: "FEEDBACK_PRAISE",
      value: 1,
      unit: "CLASSES",
      score: 5,
      courseKey: '["dom","appoint-001",1]',
      lessonId: "participation:v1:compat-display-only",
      sourceRegion: "dom",
      sourceAppointId: "appoint-001",
      participationSeq: 1,
      lessonNumber: 3,
      lessonStartedAt: "2026-07-21T08:00:00.000Z",
    },
  ]);
});

test("keeps Shiwen summary components when no class attribution is available", () => {
  assert.deepEqual(
    mergeScorecardCourseSources(
      [course],
      "reliability",
      [
        {
          sourceKey: "ON_TIME_COMPLETED",
          value: 1,
          unit: "CLASSES",
          score: 2,
          pointsPerUnit: 2,
        },
        {
          sourceKey: "PERFECT_COMPLETED",
          value: 6,
          unit: "CLASSES",
          score: 24,
          pointsPerUnit: 4,
        },
      ],
    ),
    [
      {
        sourceKey: "ON_TIME_COMPLETED",
        value: 1,
        unit: "CLASSES",
        score: 2,
        courseKey: '["dom","appoint-001",1]',
        lessonId: "participation:v1:compat-display-only",
        sourceRegion: "dom",
        sourceAppointId: "appoint-001",
        participationSeq: 1,
        lessonNumber: 3,
        lessonStartedAt: "2026-07-21T08:00:00.000Z",
        summaryScore: 2,
        summaryValue: 1,
      },
      {
        sourceKey: "PERFECT_COMPLETED",
        value: 6,
        unit: "CLASSES",
        score: 24,
        pointsPerUnit: 4,
        summaryScore: 24,
        summaryValue: 6,
        attributionMissing: true,
      },
    ],
  );
});

test("does not expose components that the source view did not award", () => {
  assert.deepEqual(
    positiveCourseScoreSources({
      ...course,
      dimensions: course.dimensions.map((dimension) => ({
        ...dimension,
        components: dimension.components.map((component) => ({
          ...component,
          awarded: false,
        })),
      })),
    }),
    [],
  );
});

test("shows only Shiwen-awarded favorite courses across the full lesson set", () => {
  const favoriteCourse = (lessonSequence, awarded) => ({
    ...course,
    lessonId: `lesson-${lessonSequence}`,
    sourceAppointId: `appoint-${lessonSequence}`,
    lessonSequence,
    facts: { ...course.facts, favorited: true },
    dimensions: [{
      code: "USER_FEEDBACK",
      components: [{
        code: "FEEDBACK_FAVORITE",
        score: awarded ? 5 : 0,
        awarded,
        evidenceStatus: "CONFIRMED",
      }],
    }],
  });
  const courses = [
    favoriteCourse(8, true),
    favoriteCourse(15, true),
    favoriteCourse(21, true),
    favoriteCourse(41, true),
    favoriteCourse(50, false),
  ];

  const sources = mergeScorecardCourseSources(courses, "userFeedback", [{
    sourceKey: "FEEDBACK_FAVORITE",
    value: 4,
    unit: "CLASSES",
    score: 20,
    pointsPerUnit: 5,
  }]);

  assert.deepEqual(
    sources.map(({ lessonNumber, score, summaryScore }) => ({
      lessonNumber,
      score,
      summaryScore,
    })),
    [
      { lessonNumber: 8, score: 5, summaryScore: 20 },
      { lessonNumber: 15, score: 5, summaryScore: 20 },
      { lessonNumber: 21, score: 5, summaryScore: 20 },
      { lessonNumber: 41, score: 5, summaryScore: 20 },
    ],
  );
});

test("uses the full participation identity instead of the compatibility lesson id", () => {
  const substituted = {
    ...course,
    lessonId: course.lessonId,
    participationSeq: 2,
  };

  assert.equal(courseParticipationKey(course), '["dom","appoint-001",1]');
  assert.equal(
    courseParticipationKey(substituted),
    '["dom","appoint-001",2]',
  );
  assert.notEqual(
    courseParticipationKey(course),
    courseParticipationKey(substituted),
  );
});

test("fails closed when a course participation identity is incomplete", () => {
  assert.throws(
    () => courseParticipationKey({ lessonId: "legacy-only" }),
    /Invalid course participation identity/,
  );
});

test("keeps a later favorite fact visible without presenting a zero score", () => {
  const laterFavorite = {
    ...course,
    facts: { ...course.facts, favorited: true },
    dimensions: [{
      code: "USER_FEEDBACK",
      components: [{
        code: "FEEDBACK_FAVORITE",
        score: 0,
        awarded: false,
        evidenceStatus: "CONFIRMED",
      }],
    }],
  };

  assert.deepEqual(positiveCourseScoreSources(laterFavorite), []);
  const favoriteFact = visibleCourseIndicators(laterFavorite).find(
    (item) => item.sourceKey === "FEEDBACK_FAVORITE",
  );
  assert.deepEqual(
    {
      value: favoriteFact.value.zh,
      tone: favoriteFact.tone,
      score: favoriteFact.score,
    },
    { value: "已被学员收藏", tone: "positive", score: null },
  );
});

test("keeps every safe course fact with a teacher-facing value", () => {
  const indicators = visibleCourseIndicators(course);
  assert.equal(indicators.length, 7);
  assert.deepEqual(
    indicators.map(({ sourceKey, value, tone, score }) => ({
      sourceKey,
      value: value.zh,
      tone,
      score,
    })),
    [
      { sourceKey: "FEEDBACK_PRAISE", value: "已收到好评", tone: "positive", score: 5 },
      { sourceKey: "FEEDBACK_FAVORITE", value: "暂无收藏", tone: "neutral", score: null },
      { sourceKey: "ON_TIME_COMPLETED", value: "准时完成", tone: "positive", score: 2 },
      { sourceKey: "RELIABILITY_NO_EARLY_LEAVE", value: "完整完成", tone: "positive", score: null },
      { sourceKey: "CLASS_QUALITY_CAMERA_ON", value: "摄像头保持开启", tone: "positive", score: null },
      { sourceKey: "CLASS_QUALITY_CPU_STABLE", value: "电脑运行稳定", tone: "positive", score: null },
      { sourceKey: "CLASS_QUALITY_NETWORK_STABLE", value: "网络连接稳定", tone: "positive", score: null },
    ],
  );
});

test("shows missing values as unavailable instead of treating them as negative facts", () => {
  const indicators = visibleCourseIndicators({
    ...course,
    facts: Object.fromEntries(
      Object.keys(course.facts).map((key) => [key, null]),
    ),
  });
  assert.equal(indicators.length, 7);
  assert.equal(indicators.every((indicator) => indicator.value.zh.includes("暂无") || indicator.value.zh.includes("不完整")), true);
  assert.equal(indicators.every((indicator) => indicator.tone === "missing"), true);
});

test("does not treat absent classes as completed course facts", () => {
  assert.deepEqual(
    visibleCourseIndicators({ ...course, lifecycleStatus: "s_absent" }),
    [],
  );
});

test("maps all current lifecycle values to teacher-facing labels", () => {
  assert.equal(lessonLifecycleStatusLabel("end").zh, "已完课");
  assert.equal(lessonLifecycleStatusLabel("s_absent").zh, "学员缺席");
  assert.equal(lessonLifecycleStatusLabel("t_absent").zh, "教师缺席");
});
