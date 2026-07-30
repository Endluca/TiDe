import { useMemo, useState } from "react";
import {
  ArrowClockwise,
  ArrowLeft,
  ArrowRight,
  Check,
  CheckCircle,
  ClipboardText,
  CloudArrowUp,
  FileArrowUp,
  HourglassMedium,
  Lightbulb,
  ShieldCheck,
  Sparkle,
  WarningCircle,
} from "@phosphor-icons/react";
import { localizeApiError } from "../../api-error-copy";
import { Toki } from "../../components/UI";
import { useI18n } from "../../i18n";
import ExternalStatusTask from "./ExternalStatusTask";
import "./profile-credentials-task.css";

function stepByRole(task, role) {
  return task.backendSteps?.find((step) => step.config?.role === role);
}

function progressFor(task, step) {
  return step ? task.backendProgressByStep?.[step.stepKey] : null;
}

function answersMatch(actual, expected) {
  if (!Array.isArray(actual) || !Array.isArray(expected)) {
    return actual === expected;
  }
  if (actual.length !== expected.length) return false;
  const normalizedActual = [...actual].sort();
  const normalizedExpected = [...expected].sort();
  return normalizedActual.every((value, index) => value === normalizedExpected[index]);
}

export default function ProfileCredentialsTask({ task }) {
  const { language } = useI18n();
  const c = (en, zh) => language === "zh" ? zh : en;
  const quizStep = stepByRole(task, "TESOL_QUIZ");
  const essayStep = stepByRole(task, "TESOL_ESSAY");
  const proofStep = stepByRole(task, "COMPLETION_PROOF");
  const quizProgress = progressFor(task, quizStep);
  const essayProgress = progressFor(task, essayStep);
  const proofProgress = progressFor(task, proofStep);
  const questions = task.quizQuestions || [];
  const [questionIndex, setQuestionIndex] = useState(0);
  const [answers, setAnswers] = useState(quizProgress?.details?.answers || {});
  const [quizResult, setQuizResult] = useState(
    quizProgress?.status === "COMPLETED" || quizProgress?.status === "FAILED"
      ? {
          ...(quizProgress?.details || {}),
          ...(quizProgress?.result || {}),
          passed: quizProgress.status === "COMPLETED",
        }
      : null,
  );
  const [retryQuestionIds, setRetryQuestionIds] = useState(null);
  const [reviewOnly, setReviewOnly] = useState(false);
  const [reviewAnswerKey, setReviewAnswerKey] = useState({});
  const [essayConfirmed, setEssayConfirmed] = useState(essayProgress?.status === "COMPLETED");
  const [proof, setProof] = useState(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");

  const statuses = task.externalStatusItems || [];
  const selfIntroComplete = statuses.find((item) => item.type === "self_intro")?.status === "approved";
  const tesolComplete = statuses.find((item) => item.type === "credential")?.status === "approved";
  const quizComplete = quizProgress?.status === "COMPLETED" || quizResult?.passed === true;
  const essayComplete = essayProgress?.status === "COMPLETED";
  const proofComplete = proofProgress?.status === "COMPLETED";
  const allComplete = selfIntroComplete && tesolComplete && quizComplete && essayComplete && proofComplete;
  const internalCompleteCount = [quizComplete, essayComplete, proofComplete].filter(Boolean).length;
  const sourceStatusTask = {
    ...task,
    statusPageVariant: "profile_credentials",
    externalStatusCopy: {
      title: "Profile review status",
      titleZh: "档案审核状态",
      description: "Review the latest Self-intro and TESOL status here.",
      descriptionZh: "在这里查看 Self-intro 与 TESOL 的最新审核状态。",
      completionTitle: "How these statuses count toward completion",
      completionTitleZh: "这两项如何计入完成条件",
      completion: "Approved Self-intro and TESOL satisfy the first two conditions. Complete the remaining three items below.",
      completionZh: "Self-intro 与 TESOL 通过后，即满足前两项条件；其余三项请继续在下方完成。",
    },
  };
  const activeQuestions = retryQuestionIds?.length
    ? questions.filter((question) => retryQuestionIds.includes(question.id))
    : questions;
  const answeredCount = useMemo(
    () => activeQuestions.filter((question) => {
      const answer = answers[question.id];
      return Array.isArray(answer) ? answer.length > 0 : answer !== undefined;
    }).length,
    [activeQuestions, answers],
  );
  const currentQuestion = activeQuestions[questionIndex];

  const selectAnswer = (question, optionIndex) => {
    if (question.type === "multiple") {
      const selected = answers[question.id] || [];
      const next = selected.includes(optionIndex)
        ? selected.filter((value) => value !== optionIndex)
        : [...selected, optionIndex];
      setAnswers((current) => ({ ...current, [question.id]: next }));
      return;
    }
    setAnswers((current) => ({ ...current, [question.id]: optionIndex }));
  };

  const run = async (key, operation) => {
    setBusy(key);
    setError("");
    try {
      return await operation();
    } catch (caught) {
      setError(localizeApiError(
        caught,
        language,
        c("The action failed. Please retry.", "操作失败，请重试。"),
      ));
      return null;
    } finally {
      setBusy("");
    }
  };

  const submitQuiz = () => run("quiz", async () => {
    if (reviewOnly) {
      const review = activeQuestions.flatMap((question) => {
        const correctAnswer = reviewAnswerKey[question.id];
        return answersMatch(answers[question.id], correctAnswer)
          ? []
          : [{
              questionKey: question.id,
              selectedAnswer: answers[question.id],
              correctAnswer,
            }];
      });
      const correct = questions.length - review.length;
      setQuizResult({
        answered: questions.length,
        total: questions.length,
        correct,
        score: Math.round((correct / questions.length) * 100),
        passed: true,
        review,
      });
      setRetryQuestionIds(null);
      return;
    }
    const response = await task.execution.saveStep("TESOL_QUIZ", { answers });
    const nextResult = {
      ...(response?.step?.result || {}),
      passed: response?.step?.status === "COMPLETED",
    };
    setQuizResult(nextResult);
    setRetryQuestionIds(null);
  });

  const answerText = (question, answer) => {
    const options = language === "zh"
      ? question?.optionsZh || question?.options || []
      : question?.options || [];
    const indexes = Array.isArray(answer) ? answer : [answer];
    return indexes
      .map((index) => options[index])
      .filter(Boolean)
      .join(c(" and ", "、")) || c("No answer", "未作答");
  };

  const incorrectReview = (Array.isArray(quizResult?.review) ? quizResult.review : [])
    .map((item) => ({
      ...item,
      question: questions.find((question) => question.id === item.questionKey),
    }))
    .filter((item) => item.question);

  const retryIncorrect = () => {
    const incorrectIds = incorrectReview.map((item) => item.questionKey);
    setReviewAnswerKey((current) => ({
      ...current,
      ...Object.fromEntries(incorrectReview.map((item) => [item.questionKey, item.correctAnswer])),
    }));
    setAnswers((current) => Object.fromEntries(
      Object.entries(current).filter(([questionId]) => !incorrectIds.includes(questionId)),
    ));
    setRetryQuestionIds(incorrectIds);
    setReviewOnly(Boolean(quizResult?.passed || quizProgress?.status === "COMPLETED"));
    setQuizResult(null);
    setQuestionIndex(0);
  };

  const saveEssay = () => run("essay", async () => {
    await task.execution.saveStep("TESOL_ESSAY", {
      checkedItemKeys: essayConfirmed ? ["essay-completed"] : [],
    });
  });

  const uploadProof = () => run("proof", async () => {
    if (!proof) return;
    await task.execution.uploadStep("COMPLETION_PROOF", proof);
  });

  const finishTask = () => run("finish", () => task.execution.submit());

  return (
    <div className="profile-credentials-task">
      <section className="g01-overview">
        <div>
          <span>{c("FIVE COMPLETION CONDITIONS", "五项完成条件")}</span>
          <h3>{c("Complete all five items to finish this task", "完成全部五项后，这项任务才算完成")}</h3>
          <p>{c(
            "Self-intro and TESOL statuses update automatically. Complete the quiz, Essay confirmation and completion proof on this page.",
            "Self-intro 与 TESOL 状态会自动更新；61 题、Essay 确认和完成证明可在本页完成。",
          )}</p>
        </div>
        <strong>{[selfIntroComplete, tesolComplete, quizComplete, essayComplete, proofComplete].filter(Boolean).length} / 5</strong>
      </section>

      <ExternalStatusTask task={sourceStatusTask} embedded />

      <section className="g01-internal-conditions">
        <header>
          <div>
            <span>{c("COMPLETE ON THIS PAGE", "在本页完成")}</span>
            <h3>{c("Finish the remaining three conditions", "继续完成其余三项条件")}</h3>
          </div>
          <strong>{internalCompleteCount} / 3</strong>
        </header>
        <div className="g01-condition-grid">
          {[
            [c("61-question check", "61 题测验"), quizComplete],
            [c("TESOL Essay", "TESOL Essay"), essayComplete],
            [c("Completion proof", "完成证明"), proofComplete],
          ].map(([label, complete]) => (
            <article className={complete ? "is-complete" : ""} key={label}>
              <ClipboardText size={23} weight="duotone" />
              <div><strong>{label}</strong><small>{complete ? c("Completed", "已完成") : c("To complete", "待完成")}</small></div>
              {complete ? <CheckCircle size={20} weight="fill" /> : <HourglassMedium size={20} />}
            </article>
          ))}
        </div>
      </section>

      <section className="g01-work-card">
        <header>
          <ShieldCheck size={25} weight="duotone" />
          <div>
            <h3>{c(
              "61-question TESOL check",
              "61 题 TESOL 测验",
            )}</h3>
            <p>{c(
              `Answer every question on this page and reach ${task.passScore || 80}% to pass.`,
              `全部题目均在本页作答，正确率达到 ${task.passScore || 80}% 即通过。`,
            )}</p>
          </div>
        </header>
        {quizResult ? (
          <div className={`quiz-result-review g01-quiz-result ${quizResult.passed ? "is-passed" : ""}`} role="status">
            <span className="result-icon">{quizResult.passed
              ? <CheckCircle size={25} weight="fill" />
              : <Lightbulb size={25} weight="fill" />}</span>
            <h3>{quizResult.passed
              ? c("You passed. Review your answers below.", "你已通过，可以在下方查看答题结果。")
              : c("Review the incorrect answers, then try them again.", "这次还未通过，先核对错题，再重做错题。")}</h3>
            <p>{c(
              `Score ${quizResult.score || 0}% · ${quizResult.correct || 0} of ${questions.length} correct`,
              `得分 ${quizResult.score || 0}% · 答对 ${quizResult.correct || 0} / ${questions.length}`,
            )}</p>
            {!quizResult.passed && (
              <div className="quiz-result-retry-toki">
                <Toki
                  mood="thumb"
                  motion="encourage"
                  alt={c("Toki encourages you to try again", "Toki 鼓励你再试一次")}
                />
                <span>
                  <strong>{c("Almost there!", "差一点就通过了！")}</strong>
                  <small>{c("The questions to review are ready below.", "需要复习的错题已经整理在下方。")}</small>
                </span>
              </div>
            )}
            {incorrectReview.length > 0 ? (
              <section className="incorrect-review" aria-label={c("Incorrect answer review", "错题回顾")}>
                <header>
                  <Lightbulb size={21} weight="fill" />
                  <div>
                    <h4>{c(
                      `Review ${incorrectReview.length} incorrect ${incorrectReview.length === 1 ? "answer" : "answers"}`,
                      `复盘 ${incorrectReview.length} 道错题`,
                    )}</h4>
                    <p>{c(
                      "Compare your answer with the correct answer, then retry the incorrect questions.",
                      "核对你的答案和正确答案，然后重做错题。",
                    )}</p>
                  </div>
                </header>
                <div className="answer-review-list">
                  {incorrectReview.map((item, index) => (
                    <article key={item.questionKey}>
                      <strong>{index + 1}. {language === "zh"
                        ? item.question.questionZh || item.question.question
                        : item.question.question}</strong>
                      <span>{c("Review needed", "回答有误")}</span>
                      <p><strong>{c("Your answer: ", "你的答案：")}</strong>{answerText(item.question, item.selectedAnswer)}</p>
                      <p><strong>{c("Correct answer: ", "正确答案：")}</strong>{answerText(item.question, item.correctAnswer)}</p>
                    </article>
                  ))}
                </div>
              </section>
            ) : quizResult.passed ? (
              <div className="g01-done"><CheckCircle size={18} weight="fill" />{c("All answers are correct.", "全部答对。")}</div>
            ) : null}
            {incorrectReview.length > 0 ? (
              <button className="primary-button" type="button" onClick={retryIncorrect}>
                <ArrowClockwise size={18} />{c("Retry incorrect answers", "重做错题")}
              </button>
            ) : !quizResult.passed ? (
              <button className="primary-button" type="button" onClick={() => {
                setAnswers({});
                setQuizResult(null);
                setRetryQuestionIds(null);
                setReviewOnly(false);
                setQuestionIndex(0);
              }}>
                <ArrowClockwise size={18} />{c("Retry check", "重新作答")}
              </button>
            ) : null}
          </div>
        ) : quizComplete && !retryQuestionIds?.length ? (
          <div className="quiz-result-review is-passed g01-quiz-result" role="status">
            <span className="result-icon"><CheckCircle size={25} weight="fill" /></span>
            <h3>{c("You passed this knowledge check.", "你已通过本次知识检查。")}</h3>
            <p>{c("The 61-question check is complete.", "61 题测验已完成。")}</p>
          </div>
        ) : currentQuestion ? (
          <div className="g01-quiz single-question-flow">
            <div className="quiz-progress">
              <span>{c(`Question ${questionIndex + 1} of ${activeQuestions.length}`, `第 ${questionIndex + 1} / ${activeQuestions.length} 题`)}</span>
              <span>{c(`${answeredCount} answered`, `已作答 ${answeredCount} 题`)}</span>
            </div>
            <fieldset className="quiz-question">
              <legend>{language === "zh" ? currentQuestion.questionZh || currentQuestion.question : currentQuestion.question}</legend>
              <div className="option-list">
                {(language === "zh" ? currentQuestion.optionsZh || currentQuestion.options : currentQuestion.options).map((option, optionIndex) => {
                  const value = answers[currentQuestion.id];
                  const selected = Array.isArray(value) ? value.includes(optionIndex) : value === optionIndex;
                  return (
                    <label className={selected ? "option-selected" : ""} key={`${currentQuestion.id}-${optionIndex}`}>
                      <input
                        type={currentQuestion.type === "multiple" ? "checkbox" : "radio"}
                        name={currentQuestion.id}
                        checked={selected}
                        onChange={() => selectAnswer(currentQuestion, optionIndex)}
                      />
                      <span className="radio-mark">{selected && currentQuestion.type === "multiple" ? <Check size={14} weight="bold" /> : null}</span>
                      {option}
                    </label>
                  );
                })}
              </div>
            </fieldset>
            <div className="question-navigation">
              <button className="secondary-button" type="button" disabled={questionIndex === 0} onClick={() => setQuestionIndex((value) => value - 1)}><ArrowLeft size={17} />{c("Previous", "上一题")}</button>
              {questionIndex < activeQuestions.length - 1 ? (
                <button className="primary-button" type="button" onClick={() => setQuestionIndex((value) => value + 1)}>{c("Next", "下一题")}<ArrowRight size={17} /></button>
              ) : (
                <button className="primary-button" type="button" disabled={answeredCount !== activeQuestions.length || busy === "quiz"} onClick={submitQuiz}>{c("Submit answers", "提交答案")}</button>
              )}
            </div>
          </div>
        ) : (
          <div className="g01-result"><WarningCircle size={22} /><span>{c("The question set is not available.", "题库暂不可用。")}</span></div>
        )}
      </section>

      <section className="g01-work-card g01-evidence-grid">
        <div>
          <header>
            <ClipboardText size={25} weight="duotone" />
            <div><h3>{c("Confirm the TESOL Essay", "确认 TESOL Essay")}</h3><p>{c("Confirm only after you have completed and submitted the required Essay.", "请在完成并提交要求的 Essay 后确认。")}</p></div>
          </header>
          {essayComplete ? (
            <div className="g01-done"><CheckCircle size={22} weight="fill" />{c("Essay completion confirmed.", "Essay 已确认完成。")}</div>
          ) : (
            <>
              <label className="g01-confirm">
                <input type="checkbox" checked={essayConfirmed} onChange={(event) => setEssayConfirmed(event.target.checked)} />
                <span>{essayConfirmed ? <Check size={16} weight="bold" /> : null}</span>
                {c("I have completed and submitted the required TESOL Essay.", "我已完成并提交要求的 TESOL Essay。")}
              </label>
              <button className="primary-button" type="button" disabled={!essayConfirmed || busy === "essay"} onClick={saveEssay}>{c("Save confirmation", "确认完成")}</button>
            </>
          )}
        </div>

        <div>
          <header>
            <FileArrowUp size={25} weight="duotone" />
            <div><h3>{c("Submit completion proof", "提交完成证明")}</h3><p>{c("Upload one clear image or PDF of the completion proof.", "上传一份清晰的完成证明图片或 PDF。")}</p></div>
          </header>
          {proofComplete ? (
            <div className="g01-done"><CheckCircle size={22} weight="fill" />{c("Completion proof submitted.", "完成证明已提交。")}</div>
          ) : (
            <>
              <label className="g01-proof-upload">
                <input type="file" accept="image/jpeg,image/png,image/webp,application/pdf" onChange={(event) => setProof(event.target.files?.[0] || null)} />
                <CloudArrowUp size={24} weight="duotone" />
                <span>{proof?.name || c("Choose image or PDF", "选择图片或 PDF")}</span>
              </label>
              <button className="primary-button" type="button" disabled={!proof || busy === "proof"} onClick={uploadProof}>{c("Upload proof", "上传证明")}</button>
            </>
          )}
        </div>
      </section>

      {error && <div className="g01-error" role="alert"><WarningCircle size={18} weight="fill" />{error}</div>}

      {task.status === "completed" ? (
        <div className="result-panel result-success g01-complete-state" role="status">
          <div className="result-copy">
            <span className="result-icon"><CheckCircle size={26} weight="fill" /></span>
            <span className="eyebrow">{c("TASK COMPLETE", "任务已完成")}</span>
            <h3>{c("One more clear step is complete.", `已完成“${task.name}”。`)}</h3>
            <p>{task.value}</p>
            {task.tagReward && <span className="reward-chip"><Sparkle size={16} weight="fill" />{c("Tag earned:", "已获得标签：")} {task.tagReward}</span>}
          </div>
          <Toki mood="celebrate" motion="celebrate" className="result-toki" />
        </div>
      ) : (
        <button className="primary-button wide-button g01-finish" type="button" disabled={!allComplete || busy === "finish"} onClick={finishTask}>
          {c("Complete this task", "完成这项任务")}
        </button>
      )}
    </div>
  );
}
