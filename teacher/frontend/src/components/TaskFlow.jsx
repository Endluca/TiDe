import { useState } from "react";
import {
  ArrowClockwise,
  ArrowLeft,
  ArrowRight,
  BookOpen,
  Camera,
  Check,
  CheckCircle,
  CloudArrowUp,
  FileVideo,
  FloppyDisk,
  HourglassMedium,
  Info,
  Lightbulb,
  Lock,
  Pause,
  PaperPlaneTilt,
  Play,
  SealCheck,
  ShieldCheck,
  Sparkle,
  UploadSimple,
  VideoCamera,
  WarningCircle,
} from "@phosphor-icons/react";
import { stageDescriptions } from "../live-catalog";
import { getLearningTaskContent } from "../data/tasks/learning-task-content";
import { useI18n } from "../i18n";
import { publicAsset } from "../public-assets";
import VideoQuizTask, { ChapterVideoLearning } from "../features/task-content/VideoQuizTask";
import VideoOnlyTask from "../features/task-content/VideoOnlyTask";
import KuozhiCourseTask from "../features/task-content/KuozhiCourseTask";
import EnvironmentPhotoTask from "../features/task-content/EnvironmentPhotoTask";
import ExternalStatusTask from "../features/task-content/ExternalStatusTask";
import ReadinessPhotoTask from "../features/task-content/ReadinessPhotoTask";
import DeviceCheckTask from "../features/task-content/DeviceCheckTask";
import EnvironmentCoachingTask from "../features/task-content/EnvironmentCoachingTask";
import ProfileCredentialsTask from "../features/task-content/ProfileCredentialsTask";
import { Toki } from "./UI";

function CompletedState({ task, onSecondary, secondaryLabel }) {
  const { language } = useI18n();
  const c = (en, zh) => language === "zh" ? zh : en;
  return (
    <div className="result-panel result-success">
      <div className="result-copy">
        <span className="result-icon"><CheckCircle size={26} weight="fill" /></span>
        <span className="eyebrow">{c("TASK COMPLETE", "任务已完成")}</span>
        <h3>{c("One more clear step is complete.", `已完成“${task.name}”。`)}</h3>
        <p>{task.value}</p>
        {task.tagReward && <span className="reward-chip"><Sparkle size={16} weight="fill" />{c("Tag earned:", "已获得标签：")} {task.tagReward}</span>}
        {onSecondary && <button className="secondary-button" type="button" onClick={onSecondary}>{secondaryLabel}</button>}
      </div>
      <Toki mood="celebrate" motion="celebrate" className="result-toki" />
    </div>
  );
}

function LockedPreview({ task }) {
  const { language } = useI18n();
  const c = (en, zh) => language === "zh" ? zh : en;
  const releaseDay = stageDescriptions.find((stage) => stage.range === (task.sourceStage || task.stage))?.releaseDay;
  const isStatusOnly = ["external_status", "content_pending"].includes(task.method);
  const isChecklist = task.method === "learning_checklist";
  const configuredLearningContent = getLearningTaskContent(task);
  const learningContent = isStatusOnly
    ? { ...configuredLearningContent, videoSrc: null, chapters: null }
    : configuredLearningContent;
  const chapters = learningContent.chapters || (learningContent.videoSrc
    ? [{ id: `${task.id}-main`, title: task.name, titleZh: task.name }]
    : []);
  const modules = isStatusOnly
    ? []
    : task.courseModules?.length
    ? task.courseModules
    : (task.steps || []).map((step, index) => ({ title: `${c("Step", "步骤")} ${index + 1}`, titleZh: `步骤 ${index + 1}`, content: step, contentZh: step }));
  const resources = task.method === "external_status" ? task.resources || [] : [];
  const questionCount = isStatusOnly
    ? 0
    : task.expectedQuestionCount
      || task.quizQuestions?.length
      || 0;
  const hasVideoPreview = Boolean(learningContent.videoSrc || chapters.length > 0);
  const previewTask = {
    ...task,
    locked: false,
    status: "available",
    videoProgress: 0,
    videoElapsed: 0,
    videoCompleted: false,
    chapterVideoState: {},
  };
  return (
    <div className="locked-preview locked-preview-detailed">
      <div className="locked-preview-banner">
        <span className="locked-preview-icon"><Lock size={24} weight="fill" /></span>
        <div>
          <span className="eyebrow">{isStatusOnly ? c("TASK PREVIEW", "任务预览") : c("FULL CONTENT PREVIEW", "完整内容预览")}</span>
          <h3>{c(
            `Complete the previous stage to unlock early${releaseDay ? `, or wait until Day ${releaseDay}` : ""}.`,
            `完成上一阶段可提前解锁${releaseDay ? `；最晚第 ${releaseDay} 天自动开放` : ""}。`,
          )}</h3>
          <p>{isStatusOnly
            ? c("You can review the available information and completion standard now. The latest result appears after this task is released.", "当前可查看已有信息和完成标准；任务开放后显示最新结果。")
            : isChecklist
              ? c("All checklist content is visible now. Confirmation actions open after this stage is released.", "现在可以查看完整清单；阶段开放后才能勾选确认并完成任务。")
              : c("All course content is visible now. Playback progress, answers and submission stay locked until release.", "课程内容现在全部可见；开放前仅播放进度、答题和提交操作保持锁定。")}</p>
          <div className="preview-standard"><SealCheck size={18} /><span><strong>{c("Completion standard", "完成标准")}</strong>{task.standard}</span></div>
        </div>
      </div>

      <div className="locked-preview-content">
        {hasVideoPreview && (
          <section className="preview-video-section">
            <header>
              <span>{c("VIDEO PREVIEW", "视频预览")}</span>
              <strong>{c("Preview playback available", "可以播放预览")}</strong>
            </header>
            <ChapterVideoLearning
              task={previewTask}
              content={learningContent}
              onUpdate={() => {}}
              onComplete={() => {}}
              preview
            />
            <div className="preview-only-note">
              <Lock size={16} weight="fill" />
              <span><strong>{c("Preview only", "仅供预览")}</strong>{c("Playback does not count toward formal progress. The practice and submission open after this stage is released.", "观看不会计入正式进度；阶段解锁后才可进入练习并提交完成。")}</span>
            </div>
          </section>
        )}

        {chapters.length > 0 && (
          <section className="preview-content-section">
            <header><span>{c("COURSE CONTENT", "课程内容")}</span><strong>{chapters.length} {c("chapters", "个章节")}</strong></header>
            <ol className="preview-chapter-list">
              {chapters.map((chapter, index) => (
                <li key={chapter.id || `${task.id}-${index}`}>
                  <span>{String(index + 1).padStart(2, "0")}</span>
                  <div><strong>{language === "zh" ? chapter.titleZh || chapter.title : chapter.title}</strong><small>{c("Video chapter · available to review now", "视频章节 · 当前可提前查看")}</small></div>
                  <Lock size={15} weight="fill" />
                </li>
              ))}
            </ol>
          </section>
        )}

        {modules.length > 0 && (
          <section className="preview-content-section">
            <header><span>{c("WHAT YOU WILL LEARN", "本课程学习内容")}</span><strong>{modules.length} {c("items", "项")}</strong></header>
            <div className="preview-module-grid">
              {modules.map((module, index) => (
                <article key={`${module.title}-${index}`}>
                  <span>{String(index + 1).padStart(2, "0")}</span>
                  <div><strong>{language === "zh" ? module.titleZh || module.title : module.title}</strong><p>{language === "zh" ? module.contentZh || module.content : module.content}</p></div>
                </article>
              ))}
            </div>
          </section>
        )}

        {(questionCount > 0 || task.method === "learning_quiz") && (
          <section className="preview-practice-card">
            <div><strong>{c("Course practice", "课程练习")}</strong><p>{questionCount > 0 ? c(`${questionCount} questions · ${learningContent.passScore}% to pass`, `${questionCount} 道题 · 正确率达到 ${learningContent.passScore}% 通过`) : c("Complete the course practice after learning.", "完成课程学习后进入配套练习。")}</p></div>
            <span><Lock size={15} weight="fill" />{c("Answers open after release", "开放后可作答")}</span>
          </section>
        )}

        {resources.length > 0 && (
          <section className="preview-content-section">
            <header><span>{c("COURSE LINKS", "课程入口")}</span><strong>{resources.length} {c("links", "个")}</strong></header>
            <div className="preview-resource-list">
              {resources.map((resource) => (
                <a href={resource.url} target="_blank" rel="noreferrer" key={resource.url}>
                  <BookOpen size={18} weight="duotone" />
                  <span><strong>{language === "zh" ? resource.titleZh || resource.title : resource.title}</strong><small>{language === "zh" ? resource.metaZh || resource.meta : resource.meta}</small></span>
                </a>
              ))}
            </div>
          </section>
        )}
      </div>
    </div>
  );
}

const sameAnswer = (answer, correct) => {
  if (Array.isArray(correct)) {
    return Array.isArray(answer)
      && answer.length === correct.length
      && [...answer].sort().every((value, index) => value === [...correct].sort()[index]);
  }
  return answer === correct;
};

function LearningQuizFlow({ task, onUpdate }) {
  const { language } = useI18n();
  const c = (en, zh) => language === "zh" ? zh : en;
  const questions = task.quizQuestions || [];
  const [videoProgress, setVideoProgress] = useState(task.videoProgress || 0);
  const [playing, setPlaying] = useState(false);
  const [questionIndex, setQuestionIndex] = useState(0);
  const [answers, setAnswers] = useState(task.quizAnswers || {});
  const [result, setResult] = useState(task.quizResult || null);

  if (task.locked) return <LockedPreview task={task} />;
  if (task.status === "completed" && !result) return <CompletedState task={task} />;

  const advanceVideo = () => {
    const next = Math.min(100, videoProgress + 25);
    setVideoProgress(next);
    setPlaying(next < 100);
    onUpdate(task.id, "started", {
      videoProgress: next,
      learningState: next === 100 ? "TO_TEST" : "TO_LEARN",
      progress: next === 100 ? 50 : Math.round(next / 2),
    });
  };

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

  const answeredCount = questions.filter((question) => {
    const answer = answers[question.id];
    return Array.isArray(answer) ? answer.length > 0 : answer !== undefined;
  }).length;

  const submitQuiz = () => {
    const correct = questions.filter((question) => sameAnswer(answers[question.id], question.correct)).length;
    const score = Math.round((correct / questions.length) * 100);
    const passed = score >= 80;
    const nextResult = { score, passed, correct };
    setResult(nextResult);
    onUpdate(task.id, passed ? "completed" : "retry_required", {
      quizResult: nextResult,
      quizAnswers: answers,
      attemptNo: (task.attemptNo || 0) + 1,
      learningState: passed ? "FINISHED" : "UNFINISHED",
      progress: passed ? 100 : 50,
    });
  };

  const retry = () => {
    setAnswers({});
    setResult(null);
    setQuestionIndex(0);
    onUpdate(task.id, "started", { quizResult: null, quizAnswers: null, learningState: "TO_TEST", progress: 50 });
  };

  const currentQuestion = questions[questionIndex];
  const correctAnswerText = (question) => {
    const indexes = Array.isArray(question.correct) ? question.correct : [question.correct];
    return indexes.map((index) => question.options[index]).join(c(" and ", "、"));
  };
  const selectedAnswerText = (question) => {
    const selected = answers[question.id];
    const indexes = Array.isArray(selected) ? selected : [selected];
    return indexes
      .map((index) => question.options[index])
      .filter(Boolean)
      .join(c(" and ", "、")) || c("No answer", "未作答");
  };
  const isQuizOpen = videoProgress === 100;
  const stateLabel = result
    ? result.passed ? c("Finished", "已完成") : c("Review and retry", "待复习重试")
    : isQuizOpen ? c("Ready for check", "待练习") : c("Learning", "学习中");

  return (
    <div className="native-learning-flow">
      <div className="learning-state-row">
        <span className="eyebrow">{c("IN-PLATFORM LEARNING", "站内学习")}</span>
        <span className={`learning-state ${result && !result.passed ? "attention" : ""}`}>{stateLabel}</span>
      </div>

      <section className="mock-video-player" aria-label={c("Learning video", "学习视频")}>
        <img src={publicAsset("/assets/illustrations/training-video-teacher.png")} alt={c("Teacher presenting the lesson", "教师讲解课程")} />
        <div className="video-shade">
          <span className="video-duration">{videoProgress}%</span>
          <button type="button" onClick={() => { setPlaying((value) => !value); if (!playing) advanceVideo(); }} aria-label={playing ? c("Pause", "暂停") : c("Play", "播放")}>
            {playing ? <Pause size={25} weight="fill" /> : <Play size={25} weight="fill" />}
          </button>
          <div>
            <strong>{task.name}</strong>
            <small>{c("1× only · Seeking opens after the first complete view", "首次观看需完整播放：不能倍速或拖动；看完后可自由回看")}</small>
          </div>
        </div>
        <div className="video-progress" aria-label={c(`${videoProgress}% watched`, `已观看 ${videoProgress}%`)}><i style={{ width: `${videoProgress}%` }} /></div>
      </section>
      {videoProgress < 100 && (
        <button className="primary-button wide-button" type="button" onClick={advanceVideo}>
          <Play size={18} weight="fill" />{c("Continue video", "继续观看")}
        </button>
      )}

      <section className={`native-quiz-shell ${isQuizOpen ? "" : "is-locked"}`} aria-disabled={!isQuizOpen}>
        <header>
          <span><ShieldCheck size={24} weight="duotone" /></span>
          <div><h3>{c("Knowledge check", "课后练习")}</h3><p>{c("One question at a time · 80% to pass", "每次展示一题 · 正确率达到 80% 即可通过")}</p></div>
          {!isQuizOpen && <Lock size={20} weight="fill" />}
        </header>
        {!isQuizOpen ? (
          <p className="quiz-locked-copy">{c("Finish the first complete video view to open the check.", "完整看完视频后即可开始练习。")}</p>
        ) : result ? (
          <div className={`quiz-result-review ${result.passed ? "is-passed" : ""}`} role="status">
            <span className="result-icon">{result.passed ? <CheckCircle size={25} weight="fill" /> : <Lightbulb size={25} weight="fill" />}</span>
            <h3>{result.passed
              ? c("You passed. Review your answers below.", "你已通过，可以在下方查看答题结果。")
              : c("Review the incorrect answers, then try again.", "这次还未通过，先核对错题，再试一次。")}</h3>
            <p>{c(`Score ${result.score}% · ${result.correct} of ${questions.length} correct`, `得分 ${result.score}% · 答对 ${result.correct} / ${questions.length}`)}</p>
            {!result.passed && (
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
            <div className="answer-review-list">
              {questions.map((question, index) => (
                <article key={question.id}>
                  <strong>{index + 1}. {question.question}</strong>
                  <span>{sameAnswer(answers[question.id], question.correct) ? c("Correct", "回答正确") : c("Review needed", "回答有误")}</span>
                  <p><strong>{c("Your answer: ", "你的答案：")}</strong>{selectedAnswerText(question)}</p>
                  <p><strong>{c("Correct answer: ", "正确答案：")}</strong>{correctAnswerText(question)}</p>
                </article>
              ))}
            </div>
            {result.passed
              ? <CompletedState task={task} />
              : <button className="primary-button" type="button" onClick={retry}><ArrowClockwise size={18} />{c("Retry check", "再试一次")}</button>}
          </div>
        ) : (
          <div className="single-question-flow">
            <div className="quiz-progress"><span>{c(`Question ${questionIndex + 1} of ${questions.length}`, `第 ${questionIndex + 1} / ${questions.length} 题`)}</span><span>{c(`${answeredCount} answered`, `已作答 ${answeredCount} 题`)}</span></div>
            <fieldset className="quiz-question">
              <legend>{currentQuestion.question}</legend>
              {currentQuestion.type === "multiple" && <p className="question-type-hint">{c("Select all that apply", "多选题：请选择所有正确选项")}</p>}
              <div className="option-list">
                {currentQuestion.options.map((option, optionIndex) => {
                  const value = answers[currentQuestion.id];
                  const selected = Array.isArray(value) ? value.includes(optionIndex) : value === optionIndex;
                  return (
                    <label key={option} className={selected ? "option-selected" : ""}>
                      <input
                        type={currentQuestion.type === "multiple" ? "checkbox" : "radio"}
                        name={currentQuestion.id}
                        checked={selected}
                        onChange={() => selectAnswer(currentQuestion, optionIndex)}
                      />
                      <span className="radio-mark">{currentQuestion.type === "multiple" && selected && <Check size={14} weight="bold" />}</span>
                      {option}
                    </label>
                  );
                })}
              </div>
            </fieldset>
            <div className="question-navigation">
              <button className="secondary-button" type="button" disabled={questionIndex === 0} onClick={() => setQuestionIndex((value) => value - 1)}><ArrowLeft size={17} />{c("Previous", "上一题")}</button>
              {questionIndex < questions.length - 1
                ? <button className="primary-button" type="button" onClick={() => setQuestionIndex((value) => value + 1)}>{c("Next", "下一题")}<ArrowRight size={17} /></button>
                : <button className="primary-button" type="button" disabled={answeredCount !== questions.length} onClick={submitQuiz}>{c("Submit answers", "提交练习")}</button>}
            </div>
          </div>
        )}
      </section>
    </div>
  );
}

function ChecklistFlow({ task, onUpdate }) {
  const { language } = useI18n();
  const c = (en, zh) => language === "zh" ? zh : en;
  const items = task.steps;
  const learningContent = getLearningTaskContent(task);
  const hasVideo = Boolean(learningContent.videoSrc || learningContent.chapters?.length);
  const videoRequired = hasVideo && Boolean(task.videoRequired);
  const videoComplete = Boolean(task.videoCompleted || Number(task.videoProgress) >= 100);
  const [checked, setChecked] = useState(task.checklistProgress || []);
  const [reviewing, setReviewing] = useState(false);
  if (task.locked) return <LockedPreview task={task} />;
  if (task.status === "completed" && !reviewing) {
    return (
      <CompletedState
        task={task}
        onSecondary={hasVideo ? () => setReviewing(true) : null}
        secondaryLabel={c("Replay video", "回看视频")}
      />
    );
  }
  if (task.status === "completed" && reviewing) {
    return (
      <div className="native-learning-flow video-quiz-task completed-video-review">
        <div className="learning-state-row">
          <span className="eyebrow">{c("VIDEO REVIEW", "视频回看")}</span>
          <span className="learning-state">{c("Task complete", "任务已完成")}</span>
        </div>
        <ChapterVideoLearning
          task={task}
          content={learningContent}
          onUpdate={() => {}}
          onComplete={() => {}}
          reviewMode
        />
        <button className="secondary-button review-result-button" type="button" onClick={() => setReviewing(false)}>
          <ArrowLeft size={18} />
          {c("Back to completion result", "返回完成结果")}
        </button>
      </div>
    );
  }

  const toggle = (index) => {
    const next = checked.includes(index) ? checked.filter((item) => item !== index) : [...checked, index];
    setChecked(next);
    onUpdate(task.id, "started", { checklistProgress: next, progress: Math.round((next.length / items.length) * 90) });
  };

  return (
    <div className="checklist-flow native-checklist-flow">
      {hasVideo && (
        <section className="checklist-reference-video">
          <div className="flow-note">
            <VideoCamera size={22} weight="fill" />
            <span>
              <strong>{videoRequired
                ? c("Required course video", "必看课程视频")
                : c("Reference course video", "课程参考视频")}</strong>
              {videoRequired
                ? c("Watch the full video here, then complete every checklist item below.", "请在本页完整看完视频，再完成下方全部清单。")
                : c("Watch it here when helpful. Task completion is based on the checklist below.", "可在本页直接观看；任务仍以完成下方清单为准。")}
            </span>
          </div>
          <ChapterVideoLearning
            task={task}
            content={learningContent}
            onUpdate={(taskId, nextStatus, patch) => onUpdate(taskId, nextStatus, {
              ...patch,
              checklistProgress: checked,
              progress: Math.round((checked.length / items.length) * 90),
            })}
            onComplete={(elapsed) => onUpdate(task.id, task.status === "available" ? "started" : task.status, {
              videoProgress: 100,
              videoElapsed: elapsed,
              videoDuration: elapsed,
              videoCompleted: true,
              checklistProgress: checked,
              progress: Math.round((checked.length / items.length) * 90),
            })}
          />
        </section>
      )}
      <div className="flow-note"><BookOpen size={22} weight="fill" /><span><strong>{c("Preparation guide", "准备指南")}</strong>{task.material}</span></div>
      <div className="guide-summary-grid">
        {task.cautions?.map((item) => <p key={item}><Lightbulb size={17} weight="fill" />{item}</p>)}
      </div>
      <div className="checklist-items">
        {items.map((item, index) => (
          <label key={item} className={checked.includes(index) ? "check-item checked" : "check-item"}>
            <input type="checkbox" checked={checked.includes(index)} onChange={() => toggle(index)} />
            <span className="check-mark">{checked.includes(index) && <Check size={16} weight="bold" />}</span>
            <span><small>{c("STEP", "步骤")} {String(index + 1).padStart(2, "0")}</small>{item}</span>
          </label>
        ))}
      </div>
      <button
        className="primary-button wide-button"
        type="button"
        disabled={checked.length !== items.length || (videoRequired && !videoComplete)}
        onClick={() => onUpdate(task.id, "completed", { progress: 100 })}
      >
        {videoRequired && !videoComplete
          ? c("Finish the video to complete", "完整看完视频后可完成")
          : c("Confirm all steps", "完成任务")}
      </button>
    </div>
  );
}

function FactualResponseFlow({ task, onUpdate }) {
  const { language } = useI18n();
  const c = (en, zh) => language === "zh" ? zh : en;
  const minCharacters = Number(task.responseConfig?.minCharacters) || 20;
  const maxCharacters = Number(task.responseConfig?.maxCharacters) || 2000;
  const draftKey = `new-teacher-camp-factual-response-${task.backendId || task.id}`;
  const localDraft = !task.execution?.live
    ? (() => {
        try {
          return localStorage.getItem(draftKey) || "";
        } catch {
          return "";
        }
      })()
    : "";
  const [response, setResponse] = useState(task.factualResponse || localDraft);
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState("");
  const trimmedResponse = response.trim();
  const canSubmit = trimmedResponse.length >= minCharacters
    && response.length <= maxCharacters;

  if (task.locked) return <LockedPreview task={task} />;
  if (task.status === "completed") return <CompletedState task={task} />;
  if (["submitting", "submitted", "verifying"].includes(task.status)) {
    return (
      <div className="factual-response-flow factual-response-submitted" role="status">
        <div className="review-waiting">
          <span className="review-orbit"><ShieldCheck size={30} weight="fill" /></span>
          <span className="eyebrow">{c("RESPONSE RECEIVED", "事实说明已收到")}</span>
          <h3>{c("Your response is under operational review.", "你的说明正在等待运营复核。")}</h3>
          <p>{c("You do not need to submit it again. The result and any next step will appear here after review.", "无需重复提交。复核完成后，这里会显示结果和需要采取的下一步。")}</p>
        </div>
        {(task.factualResponse || response) && (
          <section className="factual-response-preview" aria-label={c("Submitted factual response", "已提交的事实说明")}>
            <strong>{c("Your submitted response", "你提交的说明")}</strong>
            <p>{task.factualResponse || response}</p>
          </section>
        )}
      </div>
    );
  }

  const updateResponse = (event) => {
    const next = event.target.value;
    setResponse(next);
    setNotice("");
    if (!task.execution?.live) {
      try {
        localStorage.setItem(draftKey, next);
      } catch {
        // Mock draft persistence is best-effort when browser storage is unavailable.
      }
    }
  };

  const saveDraft = async () => {
    setSaving(true);
    setNotice("");
    try {
      await onUpdate(task.id, "started", {
        factualResponse: response,
        progress: Math.min(90, Math.round((trimmedResponse.length / minCharacters) * 90)),
      });
      setNotice(c("Draft saved.", "草稿已保存。"));
    } catch {
      setNotice(c("The draft could not be saved. Please try again.", "草稿保存失败，请重试。"));
    } finally {
      setSaving(false);
    }
  };

  const submitResponse = async () => {
    if (!canSubmit) return;
    setSaving(true);
    setNotice("");
    try {
      await onUpdate(task.id, "submitted", {
        factualResponse: trimmedResponse,
        factualResponseSubmitted: true,
        progress: 100,
      });
      if (!task.execution?.live) {
        try {
          localStorage.removeItem(draftKey);
        } catch {
          // The submitted Mock response remains in component state.
        }
      }
    } catch {
      setNotice(c("Submission failed. Your draft is still here; please try again.", "提交失败，草稿仍然保留，请重试。"));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="factual-response-flow">
      {task.status === "retry_required" && (
        <div className="feedback-note" role="alert">
          <WarningCircle size={22} weight="fill" />
          <span>
            <strong>{c("Your earlier response is saved.", "之前的说明已经保留。")}</strong>
            {c("Update only the facts requested in the review note, then submit again.", "请只补充复核说明中要求的事实，再重新提交。")}
          </span>
        </div>
      )}
      <div className="flow-note">
        <ShieldCheck size={22} weight="fill" />
        <span>
          <strong>{c("Write only verifiable classroom facts", "请只填写可核实的课堂事实")}</strong>
          {c("Include what happened, what you did during the class, and any follow-up. Do not guess who submitted feedback or include learner identity.", "可以说明发生了什么、你在课堂中的处理以及后续动作；不要猜测反馈人，也不要填写学员身份信息。")}
        </span>
      </div>
      <div className="guide-summary-grid factual-response-guidance">
        {task.cautions?.map((item) => <p key={item}><Lightbulb size={17} weight="fill" />{item}</p>)}
      </div>
      <label className="factual-response-field">
        <span>
          <strong>{c("Factual response", "事实说明")}</strong>
          <small>{c(`Required · ${minCharacters}–${maxCharacters} characters`, `必填 · ${minCharacters}–${maxCharacters} 字`)}</small>
        </span>
        <textarea
          value={response}
          minLength={minCharacters}
          maxLength={maxCharacters}
          rows="8"
          placeholder={c(
            "Example: In the class on [date/time], I observed… I responded by… After class, I…",
            "示例：在[日期/时间]的课程中，我观察到……我当时采取了……课后我又……",
          )}
          aria-describedby="factual-response-help factual-response-count"
          onChange={updateResponse}
        />
      </label>
      <div className="factual-response-meta">
        <small id="factual-response-help">
          {trimmedResponse.length < minCharacters
            ? c(
                `${minCharacters - trimmedResponse.length} more characters are needed before submission.`,
                `还需填写 ${minCharacters - trimmedResponse.length} 字才能提交。`,
              )
            : c("The response is ready to submit.", "说明已达到提交要求。")}
        </small>
        <strong id="factual-response-count" className={response.length >= maxCharacters ? "is-limit" : ""}>
          {response.length} / {maxCharacters}
        </strong>
      </div>
      {notice && <p className="factual-response-notice" role="status">{notice}</p>}
      <div className="factual-response-actions">
        <button className="secondary-button" type="button" disabled={saving} onClick={saveDraft}>
          <FloppyDisk size={18} />{saving ? c("Saving…", "保存中…") : c("Save draft", "保存草稿")}
        </button>
        <button className="primary-button" type="button" disabled={saving || !canSubmit} onClick={submitResponse}>
          <PaperPlaneTilt size={18} />{saving ? c("Submitting…", "提交中…") : c("Submit for review", "提交运营复核")}
        </button>
      </div>
    </div>
  );
}

function GuidanceAcknowledgementFlow({ task, onUpdate }) {
  const { language } = useI18n();
  const c = (en, zh) => language === "zh" ? zh : en;
  const [reviewed, setReviewed] = useState(task.guidanceChecks || []);
  const [answers, setAnswers] = useState(task.guidanceAnswers || {});
  const questions = task.verificationQuestions || [];
  const allQuestionsAnswered = questions.every((question) => typeof answers[question.id] === "boolean");
  const checkCompleted = answers.checkCompleted === true;
  const checkPassed = answers.checkPassed === true;
  const canCloseReminder = checkCompleted && checkPassed;

  if (task.status === "completed") return <CompletedState task={task} />;

  const toggle = (index) => {
    const next = reviewed.includes(index)
      ? reviewed.filter((value) => value !== index)
      : [...reviewed, index];
    setReviewed(next);
    onUpdate(task.id, "started", {
      guidanceChecks: next,
      guidanceAnswers: answers,
      progress: Math.round((next.length / task.steps.length) * 70),
    });
  };

  const setAnswer = (questionId, value) => {
    const next = { ...answers, [questionId]: value };
    setAnswers(next);
    const answeredCount = questions.filter((question) => typeof next[question.id] === "boolean").length;
    onUpdate(task.id, "started", {
      guidanceAnswers: next,
      guidanceChecks: reviewed,
      progress: Math.min(90, Math.round((reviewed.length / task.steps.length) * 70) + Math.round((answeredCount / questions.length) * 20)),
    });
  };

  return (
    <div className="guidance-flow">
      <div className="guidance-support-note">
        <Info size={22} weight="fill" />
        <span>
          <strong>{c("A class result you can act on", "一条可以处理的课堂结果")}</strong>
          {c(
            "This reminder explains the recorded result and a practical next step. It is not a penalty or a final diagnosis.",
            "这条提醒只说明已记录的结果和可执行的下一步，不代表处罚或最终责任判断。",
          )}
        </span>
      </div>

      <div className="guidance-action-heading">
        <ShieldCheck size={22} weight="duotone" />
        <div>
          <h3>{c("Complete one check for today", "今天完成一次检测")}</h3>
          <p>{c("The affected classes are combined into this single reminder. Complete the AC/ACE check, then record the result below.", "同一天的触发课程合并为这一条提醒。完成 AC／ACE 检测后，在下方记录结果。")}</p>
        </div>
      </div>

      <div className="checklist-items guidance-checklist">
        {task.steps.map((item, index) => (
          <label key={item} className={reviewed.includes(index) ? "check-item checked" : "check-item"}>
            <input type="checkbox" checked={reviewed.includes(index)} onChange={() => toggle(index)} />
            <span className="check-mark">{reviewed.includes(index) && <Check size={16} weight="bold" />}</span>
            <span><small>{c("ACTION", "步骤")} {String(index + 1).padStart(2, "0")}</small>{item}</span>
          </label>
        ))}
      </div>

      <section className="guidance-verification" aria-label={c("Check result", "检测结果确认")}>
        {questions.map((question) => (
          <fieldset key={question.id}>
            <legend>{language === "zh" ? question.labelZh || question.label : question.label}</legend>
            <div>
              {[true, false].map((value) => (
                <button
                  className={answers[question.id] === value ? "selected" : ""}
                  type="button"
                  key={String(value)}
                  aria-pressed={answers[question.id] === value}
                  onClick={() => setAnswer(question.id, value)}
                >
                  {value ? c("Yes", "是") : c("No", "否")}
                </button>
              ))}
            </div>
          </fieldset>
        ))}
        {allQuestionsAnswered && !canCloseReminder && (
          <p>{c("The reminder stays open until the check is completed and passes.", "检测尚未完成或未通过时，这条提醒会继续保留。")}</p>
        )}
      </section>

      <button
        className="primary-button wide-button"
        type="button"
        disabled={reviewed.length !== task.steps.length || !allQuestionsAnswered}
        onClick={() => onUpdate(task.id, canCloseReminder ? "completed" : "started", {
          guidanceChecks: reviewed,
          guidanceAnswers: answers,
          progress: canCloseReminder ? 100 : 90,
        })}
      >
        {canCloseReminder ? c("Confirm and close reminder", "确认并结束提醒") : c("Save check result", "保存检测结果")}
      </button>
    </div>
  );
}

function PendingContentFlow({ task }) {
  const { language } = useI18n();
  const c = (en, zh) => language === "zh" ? zh : en;
  const personalized = task.taskCategory === "personalized";
  return (
    <div className="locked-preview locked-preview-detailed">
      <div className="locked-preview-banner">
        <span className="locked-preview-icon"><HourglassMedium size={24} weight="fill" /></span>
        <div>
          <span className="eyebrow">{personalized ? c("PERSONALIZED TASK · CONTENT PENDING", "个性化任务 · 内容待补充") : c("REQUIRED TASK · CONTENT PENDING", "必修任务 · 内容待补充")}</span>
          <h3>{personalized
            ? c("This improvement task is confirmed. Its official learning content is being prepared.", "这项改善任务已经确认，正式学习内容正在准备中。")
            : c("The task is confirmed. Its official learning content is being prepared.", "这项必修任务已经确认，正式学习内容正在准备中。")}</h3>
          <p>{task.reason}</p>
          <div className="preview-standard"><Info size={18} /><span><strong>{c("Completion method", "完成方式")}</strong>{task.standard}</span></div>
        </div>
      </div>
    </div>
  );
}

function UploadReviewFlow({ task, onUpdate }) {
  const { language } = useI18n();
  const c = (en, zh) => language === "zh" ? zh : en;
  const [video, setVideo] = useState("");
  const [credential, setCredential] = useState("");
  const hasTargetedUpdates = task.status === "retry_required"
    && Array.isArray(task.requiredUpdates)
    && task.requiredUpdates.length > 0;
  const needsVideoUpdate = !hasTargetedUpdates || task.requiredUpdates.includes("video");
  const needsCredentialUpdate = !hasTargetedUpdates || task.requiredUpdates.includes("credential");
  if (task.locked) return <LockedPreview task={task} />;
  if (task.status === "completed") return <CompletedState task={task} />;
  if (["submitting", "submitted", "verifying"].includes(task.status)) {
    const refreshReviewResult = () => {
      if ((task.attemptNo || 0) > 0) {
        onUpdate(task.id, "completed", {
          reviewStatus: "approved",
          reviewFeedback: null,
          requiredUpdates: [],
          progress: 100,
        });
        return;
      }
      onUpdate(task.id, "retry_required", {
        reviewStatus: "update_required",
        reviewFeedback: c(
          "Your Self-intro is accepted. Please replace the credential image with a clearer copy.",
          "自我介绍视频已通过；请仅补充一份更清晰的资质材料。",
        ),
        requiredUpdates: ["credential"],
        progress: 75,
      });
    };
    return (
      <div className="review-state teacher-review-state" role="status">
        <span className="review-orbit"><CloudArrowUp size={30} weight="fill" /></span>
        <span className="eyebrow">{c("FILES RECEIVED", "材料已收到")}</span>
        <h3>{c("Your evidence is under review.", "材料审核中。")}</h3>
        <p>{c("No repeated upload is needed. A teacher-visible result and next action will appear here after verification.", "无需重复上传。审核完成后会显示结果；如需修改，会告诉你具体是哪份材料。")}</p>
        <div className="evidence-review-list">
          <div><FileVideo size={20} /><span><strong>{c("Self-intro", "自我介绍视频")}</strong><small>{c("AI pre-check and manual verification", "AI 初审和人工复核")}</small></span><em>{c("Under review", "审核中")}</em></div>
          <div><SealCheck size={20} /><span><strong>TESOL</strong><small>{c("Credential verification", "资质审核")}</small></span><em>{c("Under review", "审核中")}</em></div>
        </div>
        <button className="secondary-button" type="button" onClick={refreshReviewResult}><ArrowClockwise size={18} />{c("Refresh review status", "查看最新审核状态")}</button>
        <Toki mood="happy" motion="scan" loop className="review-toki" />
      </div>
    );
  }

  return (
    <div className="upload-flow">
      {task.status === "retry_required" && <div className="feedback-note" role="alert"><WarningCircle size={22} weight="fill" /><span><strong>{c("Your completed progress is saved.", "已通过的材料无需重新提交。")}</strong>{task.reviewFeedback || c("Please update only the item mentioned in the verification note, then submit again.", "请根据审核说明，只更新需要调整的材料后再次提交。")}</span></div>}
      <div className="upload-grid">
        {needsVideoUpdate ? (
          <label className={video ? "upload-slot file-ready" : "upload-slot"}>
            <input type="file" accept="video/mp4,video/quicktime" onChange={(event) => setVideo(event.target.files?.[0]?.name || "")} />
            <span className="upload-icon"><FileVideo size={26} /></span>
            <strong>{video || c("Self-intro video", "Self-intro 视频")}</strong>
            <small>{video ? c("Ready to submit", "可以提交") : c("MP4 or MOV · up to 200 MB", "MP4 或 MOV · 不超过 200 MB")}</small>
            <span className="secondary-button"><UploadSimple size={17} />{c("Choose file", "选择文件")}</span>
          </label>
        ) : (
          <div className="upload-slot file-ready"><span className="upload-icon"><CheckCircle size={26} /></span><strong>Self-intro</strong><small>{c("Accepted · No need to upload again", "已通过 · 无需重复上传")}</small></div>
        )}
        {needsCredentialUpdate ? (
          <label className={credential ? "upload-slot file-ready" : "upload-slot"}>
            <input type="file" accept="image/jpeg,image/png,.pdf" onChange={(event) => setCredential(event.target.files?.[0]?.name || "")} />
            <span className="upload-icon"><SealCheck size={26} /></span>
            <strong>{credential || c("TESOL credential", "TESOL 资质")}</strong>
            <small>{credential ? c("Ready to submit", "可以提交") : c("PDF, JPG or PNG · up to 20 MB", "PDF、JPG 或 PNG · 不超过 20 MB")}</small>
            <span className="secondary-button"><UploadSimple size={17} />{c("Choose file", "选择文件")}</span>
          </label>
        ) : (
          <div className="upload-slot file-ready"><span className="upload-icon"><CheckCircle size={26} /></span><strong>TESOL</strong><small>{c("Accepted · No need to upload again", "已通过 · 无需重复上传")}</small></div>
        )}
      </div>
      <button
        className="primary-button wide-button"
        type="button"
        disabled={(needsVideoUpdate && !video) || (needsCredentialUpdate && !credential)}
        onClick={() => onUpdate(task.id, "verifying", {
          progress: 75,
          submittedFiles: [video, credential].filter(Boolean),
          attemptNo: (task.attemptNo || 0) + 1,
          reviewStatus: "pending",
          reviewFeedback: null,
          requiredUpdates: [],
        })}
      >{c("Submit for review", "提交审核")}</button>
    </div>
  );
}

function EnvironmentPhotoFlow({ task, onUpdate }) {
  const { language } = useI18n();
  const c = (en, zh) => language === "zh" ? zh : en;
  const [checked, setChecked] = useState(task.environmentChecks || []);
  const [permission, setPermission] = useState("idle");
  const [photo, setPhoto] = useState(false);
  const [redo, setRedo] = useState(false);
  if (task.locked) return <LockedPreview task={task} />;
  if (task.status === "completed" && !redo) return <CompletedState task={task} onSecondary={() => setRedo(true)} secondaryLabel={c("Review setup again", "再次检查上课环境")} />;
  const standards = [
    c("Camera view is bright and keeps your face visible", "画面光线清晰，面部完整可见"),
    c("Microphone and speakers are ready for class", "麦克风和扬声器已可正常使用"),
    c("The teaching area is tidy and free from private information", "授课区域整洁，画面中没有个人隐私信息"),
  ];
  const toggle = (index) => {
    const next = checked.includes(index) ? checked.filter((value) => value !== index) : [...checked, index];
    setChecked(next);
    onUpdate(task.id, "started", { environmentChecks: next, progress: Math.round((next.length / standards.length) * 45) });
  };
  return (
    <div className="environment-photo-flow">
      <div className="flow-note"><ShieldCheck size={22} weight="fill" /><span><strong>{c("Classroom setup standard", "授课环境标准")}</strong>{c("Confirm the visible setup, then submit one classroom photo. No hardware score is calculated here.", "先检查画面、声音和教学区域，再拍一张照片提交。")}</span></div>
      <div className="environment-checks">
        {standards.map((item, index) => (
          <label key={item} className={checked.includes(index) ? "checked" : ""}><input type="checkbox" checked={checked.includes(index)} onChange={() => toggle(index)} /><span>{checked.includes(index) && <Check size={15} weight="bold" />}</span>{item}</label>
        ))}
      </div>
      <section className="camera-permission-card">
        <header><Camera size={24} weight="duotone" /><div><h3>{c("Classroom photo", "授课环境照片")}</h3><p>{c("Used to confirm the visible classroom setup.", "用于确认可见的授课环境。")}</p></div></header>
        {permission === "idle" && <div className="camera-actions"><button className="primary-button" type="button" onClick={() => setPermission("allowed")}><VideoCamera size={18} />{c("Allow camera", "允许相机")}</button><button className="text-button" type="button" onClick={() => setPermission("help")}>{c("I can’t enable camera", "相机打不开？")}</button></div>}
        {permission === "help" && <div className="camera-help" role="alert"><WarningCircle size={20} weight="fill" /><span><strong>{c("Camera permission is not enabled.", "相机权限尚未开启。")}</strong>{c("Open browser site settings, allow camera access, then return and try again. You can still review the standard above.", "请在浏览器站点设置中允许相机权限，再返回重试；你仍可先查看上方标准。")}</span><button className="secondary-button" type="button" onClick={() => setPermission("idle")}>{c("Try again", "重试")}</button></div>}
        {permission === "allowed" && (
          <div className="mock-camera-stage">
            <img src={publicAsset("/assets/illustrations/training-video-teacher.png")} alt={c("Classroom camera preview", "授课环境相机预览")} />
            <span>{c("CAMERA PREVIEW", "相机预览")}</span>
            {!photo ? <button className="primary-button" type="button" onClick={() => setPhoto(true)}><Camera size={18} />{c("Take photo", "拍摄照片")}</button> : <div className="photo-confirm"><CheckCircle size={20} weight="fill" /><strong>{c("Photo ready", "照片已拍摄")}</strong><button className="secondary-button" type="button" onClick={() => setPhoto(false)}>{c("Retake", "重拍")}</button></div>}
          </div>
        )}
      </section>
      <button className="primary-button wide-button" type="button" disabled={checked.length !== standards.length || !photo} onClick={() => { setRedo(false); onUpdate(task.id, "completed", { environmentChecks: checked, photoSubmitted: true, progress: 100 }); }}>{c("Submit setup confirmation", "提交并完成任务")}</button>
    </div>
  );
}

function EmbeddedCourseFlow({ task, onUpdate }) {
  const { language } = useI18n();
  const c = (en, zh) => language === "zh" ? zh : en;
  const [reviewed, setReviewed] = useState(task.courseSections || []);
  if (task.locked) return <LockedPreview task={task} />;
  if (task.status === "completed") return <CompletedState task={task} />;
  const toggle = (index) => {
    const next = reviewed.includes(index) ? reviewed.filter((value) => value !== index) : [...reviewed, index];
    setReviewed(next);
    onUpdate(task.id, "started", { courseSections: next, progress: Math.round((next.length / task.steps.length) * 90) });
  };
  return (
    <div className="embedded-course-flow">
      <div className="course-cover"><img src={publicAsset("/assets/illustrations/training-video-teacher.png")} alt="" aria-hidden="true" /><span><BookOpen size={24} weight="fill" />{c("IN-PLATFORM COURSE", "站内课程")}</span><h3>{task.name}</h3><p>{task.material}</p></div>
      <div className="course-section-list">
        {task.steps.map((step, index) => <button type="button" key={step} className={reviewed.includes(index) ? "completed" : ""} onClick={() => toggle(index)}><span>{reviewed.includes(index) ? <Check size={16} weight="bold" /> : index + 1}</span><strong>{step}</strong><small>{reviewed.includes(index) ? c("Reviewed", "已学习") : c("Open section", "开始学习")}</small></button>)}
      </div>
      <button className="primary-button wide-button" type="button" disabled={reviewed.length !== task.steps.length} onClick={() => onUpdate(task.id, "completed", { progress: 100 })}>{c("Confirm course completion", "完成课程")}</button>
    </div>
  );
}

const flowComponents = {
  content_pending: PendingContentFlow,
  learning_checklist: ChecklistFlow,
  factual_response: FactualResponseFlow,
  upload_review: UploadReviewFlow,
  guidance_acknowledgement: GuidanceAcknowledgementFlow,
};

export default function TaskFlow({ task, onUpdate, onHelp }) {
  if (task.locked) return <LockedPreview task={task} />;
  if (task.method === "external_course") {
    return <KuozhiCourseTask task={task} />;
  }
  if (["learning_quiz", "document_quiz"].includes(task.method) && !task.locked) {
    return <VideoQuizTask task={task} onUpdate={onUpdate} />;
  }
  if (task.method === "video_learning") {
    return <VideoOnlyTask task={task} onUpdate={onUpdate} />;
  }
  if (task.method === "environment_photo") {
    return <EnvironmentPhotoTask task={task} onUpdate={onUpdate} />;
  }
  if (task.method === "readiness_photo") {
    return <ReadinessPhotoTask task={task} />;
  }
  if (task.method === "device_check") {
    return <DeviceCheckTask task={task} />;
  }
  if (task.method === "environment_coaching") {
    return <EnvironmentCoachingTask task={task} />;
  }
  if (task.method === "external_status") {
    return <ExternalStatusTask task={task} onHelp={onHelp} onUpdate={onUpdate} />;
  }
  if (task.method === "profile_credentials") {
    return <ProfileCredentialsTask task={task} />;
  }
  const Flow = flowComponents[task.method];
  if (!Flow) return null;
  return <Flow task={task} onUpdate={onUpdate} />;
}
