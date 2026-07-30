import test from "node:test";
import assert from "node:assert/strict";

import {
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

test("presents teacher-readable rules from Shiwen scorecard components", () => {
  const groups = presentScorecardRules([
    {
      code: "USER_FEEDBACK",
      score: 10,
      scoreRuleVersion: "rule-v8",
      components: [
        { code: "FEEDBACK_PRAISE", pointsPerUnit: 5 },
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
      description: "世文返回的用户反馈子项按次累计，本维度不封顶。",
      currentScore: 10,
      scoreRuleVersion: "rule-v8",
      items: [{
        code: "FEEDBACK_PRAISE",
        title: "学员好评",
        condition: "每收到 1 次已确认好评",
        pointsLabel: "+5/次",
      }],
    },
    {
      code: "NEW_TEACHER_TASK",
      title: "必修任务",
      description: "每项必修任务完成后，获得世文积分卡返回的对应分值。",
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
      description: "符合条件的课程按世文返回的单课分值累计。",
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
      description: "硬件质量积分以世文当前积分卡为准。",
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
