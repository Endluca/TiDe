import assert from "node:assert/strict";
import test from "node:test";

import {
  personalizedTaskTemplates,
} from "../src/data/tasks/personalized-task-templates.js";

const expectedIds = [
  "classroom-quality-reminder",
  "attendance-reliability-refresher",
  "lesson-memo-rules-learning",
  "feedback-self-study",
  "feedback-interaction-engagement",
  "feedback-correction-explanation",
  "feedback-speaking-pace",
  "feedback-scaffolding-language",
  "feedback-teaching-aids",
  "feedback-student-response",
  "feedback-pronunciation",
  "feedback-professionalism",
  "feedback-blacklist-review",
];

test("publishes exactly the confirmed 13 teacher-facing personalized task types", () => {
  assert.equal(personalizedTaskTemplates.length, 13);
  assert.deepEqual(
    personalizedTaskTemplates.map((task) => task.id),
    expectedIds,
  );
  assert.equal(
    personalizedTaskTemplates.some((task) => "mockAssigned" in task),
    false,
  );
});

test("keeps feedback and complaint sources on the same matched learning tasks", () => {
  const matchedLearningTasks = personalizedTaskTemplates.filter(
    (task) => task.taskCodeFamilies?.includes("P-FB-NEGATIVE"),
  );

  assert.equal(matchedLearningTasks.length, 9);
  matchedLearningTasks.forEach((task) => {
    assert.ok(task.taskCodeFamilies.includes("P-FB-COMPLAINT"));
    assert.equal(
      task.triggerFields.negativeFeedback.minimumOccurrencesForTeacher,
      2,
    );
    assert.deepEqual(
      task.triggerFields.complaint.requiredFields["投诉级别"],
      ["L2", "L3", "L4"],
    );
  });
});

test("keeps classroom quality as one reminder and separates the two reliability tasks", () => {
  const qualityTask = personalizedTaskTemplates[0];
  const reliabilityTasks = personalizedTaskTemplates.filter(
    (task) => task.relatedDimension === "reliability",
  );

  assert.equal(qualityTask.id, "classroom-quality-reminder");
  assert.equal(qualityTask.taskKind, "reminder");
  assert.deepEqual(
    reliabilityTasks.map((task) => task.id),
    ["attendance-reliability-refresher", "lesson-memo-rules-learning"],
  );
});

test("keeps the blacklist teacher task pending until Jiahe publishes its content", () => {
  const blacklistTask = personalizedTaskTemplates.find(
    (task) => task.id === "feedback-blacklist-review",
  );

  assert.equal(blacklistTask.method, "content_pending");
  assert.equal(blacklistTask.status, "preview");
  assert.equal("responseConfig" in blacklistTask, false);
  assert.equal(blacklistTask.triggerFields.allOf[0].field, "是否拉黑");
  assert.equal(blacklistTask.triggerFields.allOf[0].equals, 1);
});
