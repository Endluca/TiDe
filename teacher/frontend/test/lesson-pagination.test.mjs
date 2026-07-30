import test from "node:test";
import assert from "node:assert/strict";
import {
  buildLessonPageItems,
  clampLessonPage,
  LESSONS_PER_PAGE,
} from "../src/lesson-pagination.js";

test("uses a compact 12-class page size", () => {
  assert.equal(LESSONS_PER_PAGE, 12);
});

test("clamps invalid lesson pages", () => {
  assert.equal(clampLessonPage(-2, 5), 1);
  assert.equal(clampLessonPage(9, 5), 5);
  assert.equal(clampLessonPage("bad", 5), 1);
});

test("shows every page for short course lists", () => {
  assert.deepEqual(buildLessonPageItems(2, 5), [1, 2, 3, 4, 5]);
});

test("keeps long pagination compact around the current page", () => {
  assert.deepEqual(
    buildLessonPageItems(10, 20),
    [1, "start-ellipsis", 9, 10, 11, "end-ellipsis", 20],
  );
});
