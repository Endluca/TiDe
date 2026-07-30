import { ArrowLeft, BookOpen, CheckCircle } from "@phosphor-icons/react";
import { useState } from "react";
import { getLearningTaskContent } from "../../data/tasks/learning-task-content";
import { useI18n } from "../../i18n";
import { ChapterVideoLearning, CompletedState } from "./VideoQuizTask";

export default function VideoOnlyTask({ task, onUpdate }) {
  const { language } = useI18n();
  const c = (en, zh) => language === "zh" ? zh : en;
  const content = getLearningTaskContent(task);
  const videoComplete = Boolean(task.videoCompleted || Number(task.videoProgress) >= 100);
  const [reviewing, setReviewing] = useState(false);

  if (task.status === "completed" && !reviewing) {
    return <CompletedState task={task} onReplayVideo={() => setReviewing(true)} />;
  }

  return (
    <div className="native-learning-flow video-quiz-task video-only-task">
      <div className="learning-state-row">
        <span className="eyebrow">{reviewing ? c("VIDEO REVIEW", "视频回看") : c("IN-PLATFORM TRAINING", "站内培训")}</span>
        <span className="learning-state">{videoComplete ? c("Finished", "已完成") : c("Learning", "学习中")}</span>
      </div>
      <ChapterVideoLearning
        task={task}
        content={content}
        onUpdate={reviewing ? () => {} : onUpdate}
        onComplete={reviewing
          ? () => {}
          : (elapsed) => onUpdate(task.id, "completed", {
              videoProgress: 100,
              videoElapsed: elapsed,
              videoDuration: elapsed,
              videoCompleted: true,
              learningState: "FINISHED",
              progress: 100,
            })}
        reviewMode={reviewing}
      />
      {reviewing && (
        <button className="secondary-button review-result-button" type="button" onClick={() => setReviewing(false)}>
          <ArrowLeft size={18} />
          {c("Back to completion result", "返回完成结果")}
        </button>
      )}
      <div className="flow-note video-completion-note">
        {videoComplete ? <CheckCircle size={22} weight="fill" /> : <BookOpen size={22} weight="fill" />}
        <span>
          <strong>{c("Completion rule", "完成规则")}</strong>
          {c(
            reviewing
              ? "This review does not change your completed task status."
              : "Watch every chapter to the end to complete the task. Your progress is saved automatically.",
            reviewing
              ? "回看不会改变已经完成的任务状态。"
              : "按顺序完整看完全部章节即可完成任务，观看进度会自动保留。",
          )}
        </span>
      </div>
    </div>
  );
}
