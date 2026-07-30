// The G02 check uses only the answer key supplied with the source material.
// There is intentionally no generated explanation layer for this task.
export const platformPolicyAnswerKey = {
  "overseas-nt-policies-q1": 0,
  "overseas-nt-policies-q2": 1,
  "overseas-nt-policies-q3": 2,
  "overseas-nt-policies-q4": 0,
  "overseas-nt-policies-q5": 2,
};

export const platformPolicyQuestions = [
  {
    id: "overseas-nt-policies-q1",
    type: "single",
    question: "When should a teacher submit a Leave of Absence (LOA) request?",
    questionZh: "教师在什么情况下需要提交请假（LOA）申请？",
    options: [
      "When they will not open slots for more than 7 days.",
      "Whenever they need one day without a lesson.",
      "Only after 59 inactive days.",
      "Only when Teacher Support asks them to.",
    ],
    optionsZh: [
      "连续超过 7 天不开放时段时。",
      "只要有一天没有课程时。",
      "连续不活跃 59 天后才需要。",
      "只有 Teacher Support 要求时才需要。",
    ],
    explanation: "",
    explanationZh: "",
  },
  {
    id: "overseas-nt-policies-q2",
    type: "single",
    question: "What unlocks AoS for a newly launched HBT?",
    questionZh: "新上线 HBT 的 AoS 如何解锁？",
    options: [
      "Submitting a Lesson Memo.",
      "Completing TTP Orientation; AoS is also unlocked on day 8 after launch regardless of attendance.",
      "Opening one peak slot.",
      "Waiting for the first payout.",
    ],
    optionsZh: [
      "提交一份 Lesson Memo。",
      "完成 TTP Orientation；无论是否参加培训，上线后第 8 天也会解锁。",
      "开放一个高峰时段。",
      "等待第一次课酬到账。",
    ],
    explanation: "",
    explanationZh: "",
  },
  {
    id: "overseas-nt-policies-q3",
    type: "single",
    question: "How many attendance points are issued on the 1st of each month?",
    questionZh: "每月 1 日会发放多少出席点数？",
    options: ["2 points.", "3 points.", "4 points.", "10 points."],
    optionsZh: ["2 点。", "3 点。", "4 点。", "10 点。"],
    explanation: "",
    explanationZh: "",
  },
  {
    id: "overseas-nt-policies-q4",
    type: "single",
    question: "When should a Lesson Memo normally be completed?",
    questionZh: "Lesson Memo 通常应该在什么时候完成？",
    options: [
      "Immediately after the lesson, ideally within 5 minutes.",
      "Before the lesson starts.",
      "At the end of the month.",
      "Only when a student requests one.",
    ],
    optionsZh: [
      "课程结束后立即完成，建议在 5 分钟内。",
      "课程开始前。",
      "每月月底。",
      "只有学员提出要求时。",
    ],
    explanation: "",
    explanationZh: "",
  },
  {
    id: "overseas-nt-policies-q5",
    type: "single",
    question: "How should a teacher update bank details or raise a payout inquiry?",
    questionZh: "教师应该如何修改银行信息或咨询课酬到账问题？",
    options: [
      "Ask a student to contact support.",
      "Post the details in a public group.",
      "Submit a ticket to the Fees Team through the MyPage AI chatbot.",
      "Change the details inside a Lesson Memo.",
    ],
    optionsZh: [
      "请学员联系支持团队。",
      "把信息发到公开群。",
      "通过 MyPage AI chatbot 向 Fees Team 提交工单。",
      "在 Lesson Memo 中修改。",
    ],
    explanation: "",
    explanationZh: "",
  },
];

const legacyPlatformPolicyAnswerKey = {
  "course-499-q1": 2,
  "course-499-q2": 1,
  "course-499-q3": 0,
  "course-499-q4": 3,
  "course-499-q5": 2,
};

const answersMatch = (selected, correct) => {
  if (Array.isArray(correct)) {
    if (!Array.isArray(selected) || selected.length !== correct.length) return false;
    const selectedValues = [...selected].sort();
    const correctValues = [...correct].sort();
    return selectedValues.every((value, index) => value === correctValues[index]);
  }
  return selected === correct;
};

export function evaluatePlatformPolicyAnswers(questions, answers) {
  const review = [];
  let correct = 0;
  questions.forEach((question) => {
    const correctAnswer = platformPolicyAnswerKey[question.id];
    if (correctAnswer === undefined) return;
    if (answersMatch(answers[question.id], correctAnswer)) {
      correct += 1;
      return;
    }
    review.push({
      questionKey: question.id,
      selectedAnswer: answers[question.id],
      correctAnswer,
    });
  });
  const total = questions.length;
  const score = total ? Math.round((correct / total) * 100) : 0;
  return { answered: Object.keys(answers).length, total, correct, score, review };
}

export function buildLegacyPlatformPolicySubmission(answers) {
  const sourceQuestionIds = Object.keys(platformPolicyAnswerKey);
  const legacyQuestionIds = Object.keys(legacyPlatformPolicyAnswerKey);
  return Object.fromEntries(legacyQuestionIds.map((legacyQuestionId, index) => {
    const sourceQuestionId = sourceQuestionIds[index];
    const sourceCorrect = answersMatch(
      answers[sourceQuestionId],
      platformPolicyAnswerKey[sourceQuestionId],
    );
    const legacyCorrect = legacyPlatformPolicyAnswerKey[legacyQuestionId];
    return [legacyQuestionId, sourceCorrect ? legacyCorrect : (legacyCorrect + 1) % 4];
  }));
}
