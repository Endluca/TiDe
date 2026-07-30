import assert from "node:assert/strict";
import test from "node:test";
import {
  buildCourseAbilityTags,
  courseAbilityTagForTask,
  courseAbilityTagCatalog,
} from "../src/course-ability-tags.js";

test("always exposes the two course ability tags on the home page", () => {
  const tags = buildCourseAbilityTags([], "zh");

  assert.equal(courseAbilityTagCatalog.length, 2);
  assert.deepEqual(
    tags.map((tag) => [tag.taskId, tag.label, tag.state]),
    [
      ["cocos-training", "Cocos 课程准备", "syncing"],
      ["set-fundamentals", "SET 课程准备", "syncing"],
    ],
  );
});

test("derives earned and pending states from the matching task", () => {
  const tags = buildCourseAbilityTags([
    { taskCode: "G08", id: "cocos-training", status: "completed" },
    { taskCode: "G09", id: "set-fundamentals", status: "started" },
  ], "zh");

  assert.equal(tags[0].state, "earned");
  assert.equal(tags[1].state, "pending");
});

test("accepts the shared COMPLETED status without a tag field", () => {
  const [tag] = buildCourseAbilityTags([
    { taskCode: "G08", id: "cocos-training", backendStatus: "COMPLETED", status: "available" },
  ]);

  assert.equal(tag.state, "earned");
});

test("finds the completion reward from the course task identity", () => {
  const reward = courseAbilityTagForTask({ taskCode: "G09" }, "zh");

  assert.equal(reward.label, "SET 课程准备");
  assert.equal(courseAbilityTagForTask({ taskCode: "G07" }, "zh"), null);
});
