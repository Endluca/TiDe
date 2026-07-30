import assert from "node:assert/strict";
import test from "node:test";
import {
  buildLegacyPlatformPolicySubmission,
  evaluatePlatformPolicyAnswers,
} from "../src/data/tasks/platform-policy-quiz.js";

const questions = Array.from({ length: 5 }, (_value, index) => ({
  id: `overseas-nt-policies-q${index + 1}`,
}));

test("G02 review contains only missed-question answers from the source key", () => {
  const result = evaluatePlatformPolicyAnswers(questions, {
    "overseas-nt-policies-q1": 0,
    "overseas-nt-policies-q2": 1,
    "overseas-nt-policies-q3": 0,
    "overseas-nt-policies-q4": 0,
    "overseas-nt-policies-q5": 2,
  });

  assert.equal(result.score, 80);
  assert.equal(result.correct, 4);
  assert.deepEqual(result.review, [{
    questionKey: "overseas-nt-policies-q3",
    selectedAnswer: 0,
    correctAnswer: 2,
  }]);
  assert.equal("explanation" in result.review[0], false);
});

test("correcting the missed G02 answer produces a clean 100 percent result", () => {
  const result = evaluatePlatformPolicyAnswers(questions, {
    "overseas-nt-policies-q1": 0,
    "overseas-nt-policies-q2": 1,
    "overseas-nt-policies-q3": 2,
    "overseas-nt-policies-q4": 0,
    "overseas-nt-policies-q5": 2,
  });

  assert.equal(result.score, 100);
  assert.equal(result.correct, 5);
  assert.deepEqual(result.review, []);
});

test("the legacy Course 499 payload preserves the score from the source-document check", () => {
  const legacyAnswers = buildLegacyPlatformPolicySubmission({
    "overseas-nt-policies-q1": 0,
    "overseas-nt-policies-q2": 1,
    "overseas-nt-policies-q3": 0,
    "overseas-nt-policies-q4": 0,
    "overseas-nt-policies-q5": 2,
  });

  assert.deepEqual(legacyAnswers, {
    "course-499-q1": 2,
    "course-499-q2": 1,
    "course-499-q3": 1,
    "course-499-q4": 3,
    "course-499-q5": 2,
  });
});
