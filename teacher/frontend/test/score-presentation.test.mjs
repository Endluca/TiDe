import test from "node:test";
import assert from "node:assert/strict";

import {
  presentStageAction,
  presentScorecardRules,
  scoreStageStates,
} from "../src/score-presentation.js";

test("marks the gold stage as current after reaching 200", () => {
  assert.deepEqual(scoreStageStates(200, 100, 200), {
    graduation: { reached: true, current: false, gap: 0 },
    gold: { reached: true, current: true, gap: 0 },
  });
  assert.deepEqual(scoreStageStates(72.8, 100, 200), {
    graduation: { reached: false, current: true, gap: 27.2 },
    gold: { reached: false, current: false, gap: 127.2 },
  });
});

test("only presents milestone conditions the teacher can still complete", () => {
  assert.equal(
    presentStageAction({ reached: false, gap: 20 }, 2, "zh"),
    "还差 20.0 分，并完成 2 项必修任务",
  );
  assert.equal(
    presentStageAction({ reached: false, gap: 0 }, 2, "zh"),
    "还需完成 2 项必修任务",
  );
  assert.equal(
    presentStageAction({ reached: false, gap: 0 }, 0, "zh"),
    null,
  );
  assert.equal(
    presentStageAction({ reached: true, gap: 0 }, 0, "zh"),
    "已达成",
  );
});

test("describes favorite points as one eligible class per learner", () => {
  const [feedback] = presentScorecardRules([
    {
      code: "USER_FEEDBACK",
      score: 5,
      scoreRuleVersion: "rule-v8",
      components: [{ code: "FEEDBACK_FAVORITE", pointsPerUnit: 5 }],
    },
  ], "zh");

  assert.equal(feedback.description, "好评按次累计，收藏按去重学员人数累计，本维度不封顶。");
  assert.deepEqual(feedback.items, [{
    code: "FEEDBACK_FAVORITE",
    title: "学员收藏",
    condition: "每名学员首次符合计分条件的收藏课；后续收藏不重复计分",
    pointsLabel: "+5/人",
  }]);
});

test("presents teacher-readable rules from Shiwen scorecard components", () => {
  const groups = presentScorecardRules([
    {
      code: "USER_FEEDBACK",
      score: 10,
      scoreRuleVersion: "rule-v8",
      components: [
        { code: "FEEDBACK_PRAISE", pointsPerUnit: 5 },
        { code: "FEEDBACK_FAVORITE", pointsPerUnit: 5 },
        { code: "FEEDBACK_REBOOK_15D", pointsPerUnit: 0 },
      ],
    },
    {
      code: "NEW_TEACHER_TASK",
      score: 3,
      scoreRuleVersion: "rule-v8",
      components: [
        { code: "G01", pointsPerUnit: 3 },
        { code: "INTERNAL_RULE", pointsPerUnit: 99 },
      ],
    },
    {
      code: "RELIABILITY",
      score: 24,
      scoreRuleVersion: "rule-v8",
      components: [
        { code: "PERFECT_COMPLETED", pointsPerUnit: 4 },
      ],
    },
    {
      code: "CLASS_QUALITY",
      score: 2,
      scoreRuleVersion: "rule-v8",
      components: [
        { code: "CLASS_QUALITY_HARDWARE", pointsPerUnit: 2 },
      ],
    },
    {
      code: "CAPACITY",
      score: 10,
      scoreRuleVersion: "rule-v8",
      components: [{
        code: "CAPACITY_PEAK_SLOT_40",
        pointsPerUnit: null,
        score: 10,
      }],
    },
  ], "zh");

  assert.deepEqual(groups, [
    {
      code: "USER_FEEDBACK",
      title: "用户反馈",
      description: "好评按次累计，收藏按去重学员人数累计，本维度不封顶。",
      currentScore: 10,
      scoreRuleVersion: "rule-v8",
      items: [{
        code: "FEEDBACK_PRAISE",
        title: "学员好评",
        condition: "每收到 1 次已确认好评",
        pointsLabel: "+5/次",
      }, {
        code: "FEEDBACK_FAVORITE",
        title: "学员收藏",
        condition: "每名学员首次符合计分条件的收藏课；后续收藏不重复计分",
        pointsLabel: "+5/人",
      }],
    },
    {
      code: "NEW_TEACHER_TASK",
      title: "必修任务",
      description: "每项必修任务完成后，获得对应积分。",
      currentScore: 3,
      scoreRuleVersion: "rule-v8",
      items: [{
        code: "G01",
        title: "G01",
        condition: "完成 G01 必修任务",
        pointsLabel: "+3",
      }],
    },
    {
      code: "RELIABILITY",
      title: "上课稳定度",
      description: "符合条件的课程按当前积分卡中的单课分值累计。",
      currentScore: 24,
      scoreRuleVersion: "rule-v8",
      items: [{
        code: "PERFECT_COMPLETED",
        title: "完美完课",
        condition: "每节符合条件的完美完课",
        pointsLabel: "+4/节",
      }],
    },
    {
      code: "CLASS_QUALITY",
      title: "硬件质量",
      description: "硬件质量积分以当前积分卡为准。",
      currentScore: 2,
      scoreRuleVersion: "rule-v8",
      items: [{
        code: "CLASS_QUALITY_HARDWARE",
        title: "无设备网络&教学环境问题",
        condition: "每节无设备、网络及教学环境问题的课程",
        pointsLabel: "+2/节",
      }],
    },
    {
      code: "CAPACITY",
      title: "有效供给",
      description: "达到规定的可约课时目标后获得一次性积分。",
      currentScore: 10,
      scoreRuleVersion: "rule-v8",
      items: [{
        code: "CAPACITY_PEAK_SLOT_40",
        title: "高峰时段可约课时数",
        condition: "高峰时段可约课时数首次达到 40",
        pointsLabel: "+10（一次性）",
      }],
    },
  ]);
});

test("removes a score rule automatically when Shiwen removes its component", () => {
  const groups = presentScorecardRules([
    {
      code: "USER_FEEDBACK",
      score: 5,
      scoreRuleVersion: "rule-v9",
      components: [{ code: "FEEDBACK_PRAISE", pointsPerUnit: 5 }],
    },
  ], "zh");

  assert.equal(
    groups[0].items.some((item) => item.code === "FEEDBACK_REBOOK_15D"),
    false,
  );
});
