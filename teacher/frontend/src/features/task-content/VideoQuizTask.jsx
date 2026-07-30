import { useEffect, useRef, useState } from "react";
import {
  ArrowClockwise,
  ArrowLeft,
  ArrowRight,
  ArrowsIn,
  ArrowsOut,
  BookOpenText,
  Check,
  CheckCircle,
  Lightbulb,
  Lock,
  Pause,
  Play,
  ShieldCheck,
  Sparkle,
  WarningCircle,
} from "@phosphor-icons/react";
import { localizeApiError } from "../../api-error-copy";
import { getLearningTaskContent } from "../../data/tasks/learning-task-content";
import {
  buildLegacyPlatformPolicySubmission,
  evaluatePlatformPolicyAnswers,
} from "../../data/tasks/platform-policy-quiz";
import { useI18n } from "../../i18n";
import { Toki } from "../../components/UI";
import "./video-quiz-task.css";

const formatTime = (seconds) => {
  if (!Number.isFinite(seconds) || seconds < 0) return "00:00";
  const minutes = Math.floor(seconds / 60);
  const remainder = Math.floor(seconds % 60);
  return `${String(minutes).padStart(2, "0")}:${String(remainder).padStart(2, "0")}`;
};

export function CompletedState({ task, onPractice, onReplayVideo }) {
  const { language } = useI18n();
  const c = (en, zh) => language === "zh" ? zh : en;
  return (
    <div className="result-panel result-success">
      <div className="result-copy">
        <span className="result-icon"><CheckCircle size={26} weight="fill" /></span>
        <span className="eyebrow">{c("TASK COMPLETE", "任务已完成")}</span>
        <h3>{c("One more clear step is complete.", `已完成“${task.name}”。`)}</h3>
        <p>{task.value}</p>
        {task.tagReward && (
          <span className="reward-chip">
            <Sparkle size={16} weight="fill" />
            {c("Tag earned:", "已获得标签：")} {task.tagReward}
          </span>
        )}
        {(onReplayVideo || onPractice) && (
          <div className="completed-learning-actions">
            {onReplayVideo && (
              <button className="secondary-button" type="button" onClick={onReplayVideo}>
                <Play size={18} weight="fill" />
                {c("Replay video", "回看视频")}
              </button>
            )}
            {onPractice && (
              <button className="secondary-button" type="button" onClick={onPractice}>
                <ArrowClockwise size={18} />
                {c("Practice again", "重新练习")}
              </button>
            )}
          </div>
        )}
      </div>
      <Toki mood="celebrate" motion="celebrate" className="result-toki" />
    </div>
  );
}

function DocumentLearning({
  task,
  completed,
  submitting,
  error,
  onComplete,
}) {
  const { language } = useI18n();
  const c = (en, zh) => language === "zh" ? zh : en;
  const documentContent = task.documentContent || {};
  const sections = documentContent.sections || [];

  return (
    <section className="policy-document" aria-label={c("Policy document", "平台规则文档")}>
      <header className="policy-document-header">
        <span><BookOpenText size={28} weight="duotone" /></span>
        <div>
          <small>{c("REQUIRED READING", "必读文档")}</small>
          <h3>{documentContent.title || documentContent.sourceTitle || "Overseas NT Policies"}</h3>
          <p>{language === "zh" ? documentContent.introZh || documentContent.intro : documentContent.intro}</p>
        </div>
        {completed && <span className="policy-reviewed"><CheckCircle size={16} weight="fill" />{c("Reviewed", "已阅读")}</span>}
      </header>

      <div className="policy-document-sections">
        {sections.map((section, index) => {
          const items = language === "zh" ? section.itemsZh || section.items : section.items;
          return (
            <details
              key={section.key || section.title}
              open={index === 0}
            >
              <summary
                onClick={(event) => {
                  if (event.currentTarget.parentElement?.open) return;
                  task.execution?.track?.("DOCUMENT_MODULE_EXPANDED", {
                    stepKey: task.documentStepKey || "DOCUMENT",
                    stepType: "DOCUMENT",
                    interaction: String(section.key || `SECTION_${index + 1}`),
                    displayPosition: `SECTION_${index + 1}`,
                  });
                }}
              >
                <span>{String(index + 1).padStart(2, "0")}</span>
                <strong>{language === "zh" ? section.titleZh || section.title : section.title}</strong>
              </summary>
              <ul>
                {(items || []).map((item) => <li key={item}>{item}</li>)}
              </ul>
            </details>
          );
        })}
      </div>

      <footer className="policy-document-actions">
        <div>
          <small>{c("Source", "内容来源")}</small>
          <strong>{documentContent.sourceTitle || "Overseas NT Policies"}</strong>
          {documentContent.sourceUpdatedAt && <span>{documentContent.sourceUpdatedAt}</span>}
        </div>
        <button className="primary-button" type="button" disabled={completed || submitting} onClick={onComplete}>
          {completed
            ? <><Check size={17} weight="bold" />{c("Reading complete", "已完成阅读")}</>
            : submitting
              ? c("Saving…", "正在保存…")
              : c("I have read the rules and FAQ", "我已阅读规则与 FAQ")}
        </button>
      </footer>
      {error && <div className="readiness-error" role="alert">{error}</div>}
    </section>
  );
}

export function VideoLearningPlayer({
  task,
  content,
  onUpdate,
  onComplete,
  autoPlay = false,
  reviewMode = false,
}) {
  const { language } = useI18n();
  const c = (en, zh) => language === "zh" ? zh : en;
  const playerRef = useRef(null);
  const videoRef = useRef(null);
  const maxWatchedRef = useRef(Number(task.videoElapsed) || 0);
  const lastSavedProgressRef = useRef(Number(task.videoProgress) || 0);
  const lastSavedSecondsRef = useRef(Number(task.videoElapsed) || 0);
  const hasPlayedRef = useRef(false);
  const seekBlockedTrackedRef = useRef(false);
  const lastStalledAtRef = useRef(0);
  const firstViewComplete = Boolean(
    reviewMode || task.videoCompleted || Number(task.videoProgress) >= 100,
  );
  const [mediaState, setMediaState] = useState(content.videoSrc ? "loading" : "missing");
  const [playing, setPlaying] = useState(Boolean(autoPlay));
  const [buffering, setBuffering] = useState(false);
  const [duration, setDuration] = useState(Number(task.videoDuration) || 0);
  const [currentTime, setCurrentTime] = useState(Number(task.videoElapsed) || 0);
  const [videoProgress, setVideoProgress] = useState(firstViewComplete ? 100 : Number(task.videoProgress) || 0);
  const [completionError, setCompletionError] = useState("");
  const [fullscreen, setFullscreen] = useState(false);
  const liveExecution = Boolean(task.execution?.live) && !reviewMode;
  const videoStepKey = content.stepKey || task.videoStepKey || "VIDEO";
  const playbackProgress = duration > 0
    ? Math.max(0, Math.min(100, (currentTime / duration) * 100))
    : 0;
  const timelineProgress = firstViewComplete ? playbackProgress : videoProgress;
  const trackVideo = (eventName, properties = {}) => {
    task.execution?.track?.(eventName, {
      stepKey: videoStepKey,
      stepType: "VIDEO",
      chapterId: content.chapterId || undefined,
      videoPositionSeconds: Math.max(0, Math.floor(videoRef.current?.currentTime || currentTime)),
      ...properties,
    });
  };

  useEffect(() => {
    const syncFullscreenState = () => {
      const fullscreenElement = document.fullscreenElement || document.webkitFullscreenElement;
      setFullscreen(fullscreenElement === playerRef.current);
    };
    document.addEventListener("fullscreenchange", syncFullscreenState);
    document.addEventListener("webkitfullscreenchange", syncFullscreenState);
    return () => {
      document.removeEventListener("fullscreenchange", syncFullscreenState);
      document.removeEventListener("webkitfullscreenchange", syncFullscreenState);
    };
  }, []);

  const persistProgress = (nextProgress, elapsed, nextStatus = task.status) => {
    if (liveExecution) {
      void task.execution.saveVideo(videoStepKey, Math.max(0, Math.floor(elapsed))).catch(() => undefined);
    }
  };

  useEffect(() => {
    if (!liveExecution || firstViewComplete) return undefined;
    const persistCurrentPosition = () => {
      const video = videoRef.current;
      if (!video || !Number.isFinite(video.currentTime)) return;
      void task.execution
        .saveVideo(videoStepKey, Math.max(0, Math.floor(video.currentTime)))
        .catch(() => undefined);
    };
    const handleVisibilityChange = () => {
      if (document.visibilityState === "hidden") persistCurrentPosition();
    };
    document.addEventListener("visibilitychange", handleVisibilityChange);
    window.addEventListener("pagehide", persistCurrentPosition);
    return () => {
      document.removeEventListener("visibilitychange", handleVisibilityChange);
      window.removeEventListener("pagehide", persistCurrentPosition);
    };
  }, [firstViewComplete, liveExecution, task.execution, videoStepKey]);

  const handleLoadedMetadata = () => {
    const video = videoRef.current;
    if (!video) return;
    const nextDuration = Number.isFinite(video.duration) ? video.duration : 0;
    const savedElapsed = Math.min(Number(task.videoElapsed) || 0, nextDuration || 0);
    setDuration(nextDuration);
    video.playbackRate = content.playbackRate;
    if (reviewMode || (firstViewComplete && savedElapsed >= nextDuration - 0.5)) {
      video.currentTime = 0;
      setCurrentTime(0);
    } else if (savedElapsed > 0 && savedElapsed < nextDuration) {
      video.currentTime = savedElapsed;
      maxWatchedRef.current = savedElapsed;
      setCurrentTime(savedElapsed);
    }
    if (autoPlay) {
      setBuffering(true);
      video.play()
        .then(() => {
          const resumed = hasPlayedRef.current || video.currentTime > 1;
          hasPlayedRef.current = true;
          trackVideo(resumed ? "VIDEO_RESUMED" : "VIDEO_PLAYED", {
            isResume: resumed,
            result: "SUCCESS",
          });
          setPlaying(true);
          setBuffering(false);
        })
        .catch((caught) => {
          trackVideo("VIDEO_FAILED", {
            errorCode: caught?.name || "VIDEO_AUTOPLAY_FAILED",
            result: "FAILURE",
          });
          setPlaying(false);
          setBuffering(false);
        });
    }
  };

  const handleTimeUpdate = () => {
    const video = videoRef.current;
    if (!video || !Number.isFinite(video.duration) || video.duration <= 0) return;
    if (!firstViewComplete && video.currentTime > maxWatchedRef.current + 1.25) {
      video.currentTime = maxWatchedRef.current;
      if (!seekBlockedTrackedRef.current) {
        seekBlockedTrackedRef.current = true;
        trackVideo("VIDEO_SEEK_BLOCKED", {
          result: "BLOCKED",
          reasonCode: "FIRST_VIEW_INCOMPLETE",
        });
      }
      return;
    }
    maxWatchedRef.current = Math.max(maxWatchedRef.current, video.currentTime);
    const nextProgress = Math.min(99, Math.floor((maxWatchedRef.current / video.duration) * 100));
    setCurrentTime(video.currentTime);
    setVideoProgress(firstViewComplete ? 100 : nextProgress);
    if (!firstViewComplete && video.currentTime >= lastSavedSecondsRef.current + 10) {
      lastSavedSecondsRef.current = video.currentTime;
      lastSavedProgressRef.current = nextProgress;
      persistProgress(nextProgress, maxWatchedRef.current, task.status);
    }
  };

  const handleEnded = async () => {
    const video = videoRef.current;
    setPlaying(false);
    setBuffering(false);
    setCurrentTime(video?.duration || duration);
    setVideoProgress(100);
    const elapsed = video?.duration || duration;
    if (liveExecution) {
      try {
        const response = await task.execution.saveVideo(videoStepKey, Math.max(0, Math.floor(elapsed)));
        if (response?.step?.status !== "COMPLETED") {
          const retryFrom = Math.max(0, Math.floor(elapsed) - 10);
          await task.execution.saveVideo(videoStepKey, retryFrom);
          if (video) video.currentTime = retryFrom;
          maxWatchedRef.current = retryFrom;
          lastSavedSecondsRef.current = retryFrom;
          setCurrentTime(retryFrom);
          setVideoProgress(Number(response?.step?.percent) || 99);
          setCompletionError(c(
            "The final video progress did not sync. Replay the last few seconds, then submit the check again.",
            "视频最后一小段进度尚未同步，已退回约 10 秒；请播放至结束后再提交练习。",
          ));
          return;
        }
      } catch {
        setCompletionError(c(
          "The video completion could not be saved. Check the network and play the final few seconds again.",
          "视频完成状态暂时未保存，请检查网络后重新播放最后一小段。",
        ));
        return;
      }
    }
    setCompletionError("");
    onComplete(elapsed);
  };

  const togglePlayback = () => {
    const video = videoRef.current;
    if (!video || mediaState !== "ready") return;
    if (video.paused) {
      setCompletionError("");
      video.playbackRate = content.playbackRate;
      if (liveExecution && lastSavedSecondsRef.current === 0) {
        void task.execution
          .saveVideo(videoStepKey, Math.max(0, Math.floor(video.currentTime)))
          .catch(() => undefined);
      }
      setBuffering(true);
      void video.play()
        .then(() => {
          const resumed = hasPlayedRef.current || video.currentTime > 1;
          hasPlayedRef.current = true;
          trackVideo(resumed ? "VIDEO_RESUMED" : "VIDEO_PLAYED", {
            isResume: resumed,
            result: "SUCCESS",
          });
          setPlaying(true);
          setBuffering(false);
        })
        .catch((caught) => {
          trackVideo("VIDEO_FAILED", {
            errorCode: caught?.name || "VIDEO_PLAY_FAILED",
            result: "FAILURE",
          });
          setPlaying(false);
          setBuffering(false);
        });
      return;
    }
    video.pause();
  };

  const handlePause = () => {
    const video = videoRef.current;
    setPlaying(false);
    setBuffering(false);
    if (!video || firstViewComplete) {
      if (video && !video.ended) {
        trackVideo("VIDEO_PAUSED", { result: "PAUSED" });
      }
      return;
    }
    if (!video.ended) trackVideo("VIDEO_PAUSED", { result: "PAUSED" });
    persistProgress(
      videoProgress,
      video.currentTime,
      task.status === "available" ? "started" : task.status,
    );
  };

  const handleSeek = (event) => {
    if (!firstViewComplete || !videoRef.current) return;
    const nextTime = Number(event.target.value);
    videoRef.current.currentTime = nextTime;
    setCurrentTime(nextTime);
  };

  const seekFromPointer = (event) => {
    if (!firstViewComplete || !duration || !videoRef.current) return;
    const bounds = event.currentTarget.getBoundingClientRect();
    const ratio = Math.max(0, Math.min(1, (event.clientX - bounds.left) / bounds.width));
    const nextTime = ratio * duration;
    videoRef.current.currentTime = nextTime;
    setCurrentTime(nextTime);
  };

  const handleSeekPointerDown = (event) => {
    event.currentTarget.setPointerCapture?.(event.pointerId);
    seekFromPointer(event);
  };

  const handleSeekPointerMove = (event) => {
    if (!event.currentTarget.hasPointerCapture?.(event.pointerId)) return;
    seekFromPointer(event);
  };

  const handleSeekPointerUp = (event) => {
    seekFromPointer(event);
    event.currentTarget.releasePointerCapture?.(event.pointerId);
  };

  const toggleFullscreen = async () => {
    const player = playerRef.current;
    const video = videoRef.current;
    if (!player || !video) return;
    setCompletionError("");
    try {
      const fullscreenElement = document.fullscreenElement || document.webkitFullscreenElement;
      if (fullscreenElement) {
        if (document.exitFullscreen) {
          await document.exitFullscreen();
        } else if (document.webkitExitFullscreen) {
          document.webkitExitFullscreen();
        }
        return;
      }
      if (player.requestFullscreen) {
        await player.requestFullscreen();
      } else if (player.webkitRequestFullscreen) {
        player.webkitRequestFullscreen();
      } else if (video.webkitEnterFullscreen) {
        video.webkitEnterFullscreen();
      } else {
        throw new Error("FULLSCREEN_UNAVAILABLE");
      }
    } catch {
      setCompletionError(c(
        "Fullscreen is not available in this browser. You can continue watching in the player.",
        "当前浏览器暂不支持全屏，可继续在页面内观看。",
      ));
    }
  };

  if (["missing", "error"].includes(mediaState)) {
    return (
      <div className="quiz-material-pending" role="status">
        <WarningCircle size={22} weight="fill" />
        <span><strong>{c("Training media is not available", "培训视频暂不可用")}</strong>{c("The task will open after Jiahe's published media is available.", "待嘉荷发布正式视频后，任务会自动开放。")}</span>
      </div>
    );
  }

  return (
    <section
      ref={playerRef}
      className="mock-video-player real-video-player"
      aria-label={c("Training video", "培训视频")}
    >
      <video
        ref={videoRef}
        src={content.videoSrc}
        poster={content.poster}
        preload="auto"
        playsInline
        disablePictureInPicture
        controlsList="nodownload noplaybackrate"
        onLoadedMetadata={handleLoadedMetadata}
        onLoadStart={() => setMediaState("loading")}
        onCanPlay={() => {
          setMediaState("ready");
          setBuffering(false);
        }}
        onPlaying={() => {
          setPlaying(true);
          setBuffering(false);
        }}
        onWaiting={(event) => {
          if (!event.currentTarget.paused) setBuffering(true);
        }}
        onPause={handlePause}
        onTimeUpdate={handleTimeUpdate}
        onEnded={handleEnded}
        onError={() => {
          setBuffering(false);
          setMediaState("error");
          trackVideo("VIDEO_FAILED", {
            errorCode: "VIDEO_MEDIA_ERROR",
            result: "FAILURE",
          });
        }}
        onStalled={(event) => {
          if (!event.currentTarget.paused) setBuffering(true);
          const now = Date.now();
          if (now - lastStalledAtRef.current < 10_000) return;
          lastStalledAtRef.current = now;
          trackVideo("VIDEO_STALLED", {
            errorCode: "VIDEO_STALLED",
            result: "FAILURE",
          });
        }}
        onRateChange={(event) => {
          if (event.currentTarget.playbackRate !== content.playbackRate) {
            event.currentTarget.playbackRate = content.playbackRate;
          }
        }}
        onSeeking={(event) => {
          if (!firstViewComplete && event.currentTarget.currentTime > maxWatchedRef.current + 1.25) {
            event.currentTarget.currentTime = maxWatchedRef.current;
            if (!seekBlockedTrackedRef.current) {
              seekBlockedTrackedRef.current = true;
              trackVideo("VIDEO_SEEK_BLOCKED", {
                result: "BLOCKED",
                reasonCode: "FIRST_VIEW_INCOMPLETE",
              });
            }
          }
        }}
        onContextMenu={(event) => event.preventDefault()}
      />
      {buffering && (
        <div className="video-buffering-state" role="status" aria-live="polite">
          <span aria-hidden="true" />
          {c("Loading video…", "视频缓冲中…")}
        </div>
      )}
      <div className="video-shade">
        <button
          type="button"
          onClick={togglePlayback}
          disabled={mediaState !== "ready"}
          aria-label={playing ? c("Pause", "暂停") : c("Play", "播放")}
        >
          {playing ? <Pause size={25} weight="fill" /> : <Play size={25} weight="fill" />}
        </button>
        <div>
          <strong>{task.name}</strong>
          <small>{firstViewComplete
            ? c("First view complete · Drag the progress bar to review", "首次完整观看已完成 · 可拖动进度自由回看")
            : c("1× only · Seeking opens after the first complete view", "首次观看仅限 1 倍速且不能向前拖动")}</small>
        </div>
        <span className="video-duration">{formatTime(currentTime)} / {formatTime(duration)}</span>
        <button
          className="video-fullscreen-button"
          type="button"
          onClick={toggleFullscreen}
          disabled={mediaState !== "ready"}
          aria-label={fullscreen ? c("Exit fullscreen", "退出全屏") : c("Enter fullscreen", "全屏播放")}
          title={fullscreen ? c("Exit fullscreen", "退出全屏") : c("Enter fullscreen", "全屏播放")}
        >
          {fullscreen ? <ArrowsIn size={21} /> : <ArrowsOut size={21} />}
        </button>
      </div>
      <div className="video-progress" aria-label={c(`${videoProgress}% watched`, `已观看 ${videoProgress}%`)}>
        <i style={{ width: `${timelineProgress}%` }} />
      </div>
      <input
        className="video-seek-control"
        type="range"
        min="0"
        max={duration || 0}
        step="0.1"
        value={Math.min(currentTime, duration || 0)}
        disabled={!firstViewComplete || !duration}
        onInput={handleSeek}
        onChange={handleSeek}
        onPointerDown={handleSeekPointerDown}
        onPointerMove={handleSeekPointerMove}
        onPointerUp={handleSeekPointerUp}
        aria-label={firstViewComplete
          ? c("Seek video", "拖动视频进度")
          : c("Seeking unlocks after the first complete view", "首次完整观看后可拖动进度")}
      />
      {completionError && <div className="readiness-error" role="alert">{completionError}</div>}
    </section>
  );
}

export function ChapterVideoLearning({
  task,
  content,
  onUpdate,
  onComplete,
  preview = false,
  reviewMode = false,
}) {
  const { language } = useI18n();
  const c = (en, zh) => language === "zh" ? zh : en;
  const chapters = content.chapters || [];
  const [previewChapterState, setPreviewChapterState] = useState({});
  const savedChapterState = preview ? previewChapterState : task.chapterVideoState || {};
  const firstIncompleteIndex = chapters.findIndex((chapter) => !savedChapterState[chapter.id]?.videoCompleted);
  const [activeIndex, setActiveIndex] = useState(() => {
    if (reviewMode) return 0;
    if (task.videoCompleted) return Math.max(0, chapters.length - 1);
    return firstIncompleteIndex >= 0 ? firstIncompleteIndex : 0;
  });
  const [autoPlayNext, setAutoPlayNext] = useState(
    () => !reviewMode
      && task.chapterAutoPlayIndex != null
      && Number(task.chapterAutoPlayIndex) === (firstIncompleteIndex >= 0 ? firstIncompleteIndex : 0),
  );

  if (chapters.length === 0) {
    return (
      <VideoLearningPlayer
        task={task}
        content={content}
        onUpdate={preview ? () => {} : onUpdate}
        onComplete={onComplete}
        reviewMode={reviewMode}
      />
    );
  }

  const chapter = chapters[activeIndex] || chapters[0];
  const chapterState = savedChapterState[chapter.id] || {};
  const chapterTask = {
    ...task,
    ...chapterState,
    name: language === "zh" ? chapter.titleZh || chapter.title : chapter.title,
  };
  const chapterContent = {
    ...content,
    chapterId: chapter.id,
    videoSrc: chapter.videoSrc,
    stepKey: chapter.stepKey,
    mock: Boolean(chapter.mock),
    chapters: null,
  };

  const updateChapter = (_taskId, nextStatus, patch) => {
    const nextChapterState = {
      ...savedChapterState,
      [chapter.id]: {
        ...chapterState,
        videoProgress: patch.videoProgress,
        videoElapsed: patch.videoElapsed,
        videoDuration: patch.videoDuration,
        videoCompleted: patch.videoCompleted,
      },
    };
    const completedCount = chapters.filter((item) => nextChapterState[item.id]?.videoCompleted).length;
    const currentFraction = Math.min(1, (Number(patch.videoProgress) || 0) / 100);
    const aggregateProgress = Math.round(((completedCount + (patch.videoCompleted ? 0 : currentFraction)) / chapters.length) * 100);
    if (preview) {
      setPreviewChapterState(nextChapterState);
    } else {
      onUpdate(task.id, nextStatus, {
        chapterVideoState: nextChapterState,
        activeChapterIndex: activeIndex,
        chapterAutoPlayIndex: null,
        videoProgress: aggregateProgress,
        videoCompleted: false,
        learningState: "TO_LEARN",
        progress: Math.min(49, Math.round(aggregateProgress / 2)),
      });
    }
  };

  const completeChapter = (elapsed) => {
    const nextChapterState = {
      ...savedChapterState,
      [chapter.id]: {
        ...chapterState,
        videoProgress: 100,
        videoElapsed: elapsed,
        videoDuration: elapsed,
        videoCompleted: true,
      },
    };
    const completedCount = chapters.filter((item) => nextChapterState[item.id]?.videoCompleted).length;
    const allComplete = completedCount === chapters.length;
    const nextIndex = Math.min(activeIndex + 1, chapters.length - 1);
    const totalElapsed = Object.values(nextChapterState)
      .reduce((sum, item) => sum + (Number(item.videoElapsed) || 0), 0);

    if (preview) {
      setPreviewChapterState(nextChapterState);
    } else {
      onUpdate(task.id, task.status === "available" ? "started" : task.status, {
        chapterVideoState: nextChapterState,
        activeChapterIndex: nextIndex,
        chapterAutoPlayIndex: allComplete ? null : nextIndex,
        videoProgress: allComplete ? 100 : Math.round((completedCount / chapters.length) * 100),
        videoElapsed: totalElapsed,
        videoCompleted: allComplete,
        learningState: allComplete ? "TO_TEST" : "TO_LEARN",
        progress: allComplete ? 50 : Math.round((completedCount / chapters.length) * 50),
      });
    }

    if (allComplete) {
      onComplete(totalElapsed);
      return;
    }
    setAutoPlayNext(true);
    setActiveIndex(nextIndex);
  };

  return (
    <div className="chapter-learning-flow">
      <div className="chapter-progress-header">
        <span>{c(`Chapter ${activeIndex + 1} of ${chapters.length}`, `第 ${activeIndex + 1} / ${chapters.length} 章`)}</span>
        <strong>{language === "zh" ? chapter.titleZh || chapter.title : chapter.title}</strong>
      </div>
      <div className="chapter-strip" aria-label={c("Training chapters", "培训章节")}>
        {chapters.map((item, index) => {
          const completed = Boolean(savedChapterState[item.id]?.videoCompleted);
          const current = index === activeIndex;
          const visualState = current
            ? "current"
            : reviewMode
              ? "reviewable"
              : completed
                ? "completed"
                : "locked";
          return (
            <button
              key={item.id}
              type="button"
              className={visualState}
              disabled={!reviewMode}
              aria-current={current ? "true" : undefined}
              onClick={() => {
                setAutoPlayNext(false);
                setActiveIndex(index);
              }}
            >
              {!reviewMode && completed ? <Check size={14} weight="bold" /> : index + 1}
              {language === "zh" ? item.titleZh || item.title : item.title}
            </button>
          );
        })}
      </div>
      {chapters.length > 2 && (
        <small className="chapter-strip-hint">{c(`Swipe sideways to view all ${chapters.length} chapters`, `左右滑动查看全部 ${chapters.length} 个章节`)}</small>
      )}
      <VideoLearningPlayer
        key={`${task.id}-${chapter.id}`}
        task={chapterTask}
        content={chapterContent}
        onUpdate={updateChapter}
        onComplete={completeChapter}
        autoPlay={autoPlayNext}
        reviewMode={reviewMode}
      />
      {chapters.length > 1 && !reviewMode && !task.videoCompleted && (
        <p className="chapter-autoplay-note">{c(
          "The next chapter starts automatically after this one ends.",
          "本章播放结束后将自动连续播放下一章。",
        )}</p>
      )}
    </div>
  );
}

export default function VideoQuizTask({ task, onUpdate }) {
  const { language } = useI18n();
  const c = (en, zh) => language === "zh" ? zh : en;
  const content = getLearningTaskContent(task);
  const questions = (task.quizQuestions || []).map((question) => (
    language === "zh"
      ? {
          ...question,
          question: question.questionZh || question.question,
          options: question.optionsZh || question.options,
        }
      : question
  ));
  const [questionIndex, setQuestionIndex] = useState(0);
  const [answers, setAnswers] = useState(task.quizAnswers || {});
  const [result, setResult] = useState(task.quizResult || null);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState("");
  const [documentComplete, setDocumentComplete] = useState(Boolean(task.documentCompleted));
  const [documentSubmitting, setDocumentSubmitting] = useState(false);
  const [documentError, setDocumentError] = useState("");
  const [retryQuestionIds, setRetryQuestionIds] = useState(null);
  const [reviewOnly, setReviewOnly] = useState(false);
  const [reviewVideo, setReviewVideo] = useState(false);
  const quizUnlockedTrackedRef = useRef(false);
  const documentReadingTrackedRef = useRef(false);
  const isDocumentTask = task.method === "document_quiz";
  const hasVideo = !isDocumentTask && Boolean(content.videoSrc || content.chapters?.length);
  const videoComplete = Boolean(task.videoCompleted || Number(task.videoProgress) >= 100);
  const learningComplete = isDocumentTask ? documentComplete : videoComplete;
  const activeQuestions = retryQuestionIds?.length
    ? questions.filter((question) => retryQuestionIds.includes(question.id))
    : questions;

  useEffect(() => {
    if (
      !learningComplete
      || quizUnlockedTrackedRef.current
      || questions.length === 0
      || typeof task.execution?.trackOnce !== "function"
    ) return;
    quizUnlockedTrackedRef.current = true;
    const stepKey = task.execution?.findStep?.("QUIZ")?.stepKey || "QUIZ";
    task.execution.trackOnce("QUIZ_UNLOCKED", stepKey, {
      stepKey,
      stepType: "QUIZ",
      questionCount: questions.length,
      result: "UNLOCKED",
    });
  }, [learningComplete, questions.length, task.execution]);

  useEffect(() => {
    if (
      !isDocumentTask
      || documentComplete
      || task.status === "completed"
      || documentReadingTrackedRef.current
      || typeof task.execution?.trackOnce !== "function"
    ) return;
    documentReadingTrackedRef.current = true;
    const stepKey = task.documentStepKey || "DOCUMENT";
    task.execution.trackOnce("TASK_STEP_STARTED", stepKey, {
      stepKey,
      stepType: "DOCUMENT",
      contentType: "DOCUMENT",
      result: "STARTED",
    });
    task.execution.trackOnce("DOCUMENT_READING_STARTED", stepKey, {
      stepKey,
      stepType: "DOCUMENT",
      contentType: "DOCUMENT",
      result: "STARTED",
    });
  }, [
    documentComplete,
    isDocumentTask,
    task.documentStepKey,
    task.execution,
    task.status,
  ]);

  const startPractice = () => {
    setAnswers({});
    setResult(null);
    setDocumentComplete(false);
    setRetryQuestionIds(null);
    setQuestionIndex(0);
    setReviewOnly(true);
  };

  if (task.status === "completed" && !result && !reviewOnly) {
    if (!reviewVideo) {
      return (
        <CompletedState
          task={task}
          onPractice={task.taskCode === "G02" ? startPractice : null}
          onReplayVideo={hasVideo ? () => setReviewVideo(true) : null}
        />
      );
    }
    return (
      <div className="native-learning-flow video-quiz-task completed-video-review">
        <div className="learning-state-row">
          <span className="eyebrow">{c("VIDEO REVIEW", "视频回看")}</span>
          <span className="learning-state">{c("Task complete", "任务已完成")}</span>
        </div>
        <ChapterVideoLearning
          task={task}
          content={content}
          onUpdate={() => {}}
          onComplete={() => {}}
          reviewMode
        />
        <button className="secondary-button review-result-button" type="button" onClick={() => setReviewVideo(false)}>
          <ArrowLeft size={18} />
          {c("Back to completion result", "返回完成结果")}
        </button>
      </div>
    );
  }

  const selectAnswer = (question, optionIndex) => {
    const stepKey = task.execution?.findStep?.("QUIZ")?.stepKey || "QUIZ";
    task.execution?.trackOnce?.("TASK_STEP_STARTED", stepKey, {
      stepKey,
      stepType: "QUIZ",
      result: "STARTED",
    });
    task.execution?.trackOnce?.("QUIZ_STARTED", stepKey, {
      stepKey,
      stepType: "QUIZ",
      questionCount: questions.length,
      result: "STARTED",
    });
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

  const answeredCount = activeQuestions.filter((question) => {
    const answer = answers[question.id];
    return Array.isArray(answer) ? answer.length > 0 : answer !== undefined;
  }).length;

  const submitQuiz = async () => {
    if (questions.length === 0) return;
    if (!task.execution?.live) {
      setSubmitError(c("This task is not connected to live execution.", "当前任务未连接到正式执行服务。"));
      return;
    }
    setSubmitting(true);
    setSubmitError("");
    try {
      const sourceResult = task.taskCode === "G02"
        ? evaluatePlatformPolicyAnswers(questions, answers)
        : null;
      if (reviewOnly && sourceResult) {
        setResult({
          ...sourceResult,
          passed: sourceResult.score >= Number(content.passScore || 80),
        });
        setRetryQuestionIds(null);
        return;
      }
      const submittedAnswers = task.legacyPolicyQuiz
        ? buildLegacyPlatformPolicySubmission(answers)
        : answers;
      const response = await task.execution.saveStep("QUIZ", { answers: submittedAnswers });
      const responseResult = response?.step?.result || {};
      const review = task.taskCode === "G02"
        ? sourceResult?.review || []
        : Array.isArray(responseResult.review) && responseResult.review.length > 0
          ? responseResult.review
          : responseResult.review;
      const nextResult = {
        ...(sourceResult || {}),
        ...responseResult,
        review,
        passed: response?.step?.status === "COMPLETED",
      };
      setResult(nextResult);
      setRetryQuestionIds(null);
      if (nextResult.passed) await task.execution.submit();
    } catch (caught) {
      if (caught?.code === "PREVIOUS_STEP_INCOMPLETE") {
        await task.execution.refresh?.().catch(() => undefined);
        setSubmitError(c(
          "A video step has not finished syncing yet. Complete the unfinished chapter above, then submit again.",
          "仍有一段视频的完成状态尚未同步，请按上方提示补播未完成章节后再次提交。",
        ));
      } else {
        setSubmitError(localizeApiError(
          caught,
          language,
          c("The quiz could not be submitted. Please retry.", "测验提交失败，请重试。"),
        ));
      }
    } finally {
      setSubmitting(false);
    }
  };

  const retry = (questionIds = null) => {
    if (questionIds?.length) {
      const incorrect = new Set(questionIds);
      setAnswers((current) => Object.fromEntries(
        Object.entries(current).filter(([questionId]) => !incorrect.has(questionId)),
      ));
      setRetryQuestionIds(questionIds);
      setReviewOnly(Boolean(result?.passed || task.status === "completed"));
    } else {
      setAnswers({});
      setRetryQuestionIds(null);
      setReviewOnly(false);
    }
    setResult(null);
    setQuestionIndex(0);
  };

  const completeDocument = async () => {
    if (reviewOnly && task.status === "completed") {
      setDocumentComplete(true);
      return;
    }
    if (!task.documentStepKey) {
      setDocumentError(c(
        "This task needs the latest document configuration. Refresh and try again.",
        "任务文档配置需要更新，请刷新后重试。",
      ));
      return;
    }
    if (!task.execution?.live) {
      setDocumentError(c("This task is not connected to live execution.", "当前任务未连接到正式执行服务。"));
      return;
    }
    setDocumentSubmitting(true);
    setDocumentError("");
    try {
      const response = await task.execution.saveStep(task.documentStepKey || "DOCUMENT", { acknowledged: true });
      if (response?.step?.status === "COMPLETED") {
        setDocumentComplete(true);
      }
    } catch (caught) {
      setDocumentError(caught.message);
    } finally {
      setDocumentSubmitting(false);
    }
  };

  const answerText = (question, answer) => {
    const indexes = Array.isArray(answer) ? answer : [answer];
    return indexes
      .map((index) => question?.options?.[index])
      .filter(Boolean)
      .join(c(" and ", "、")) || c("No answer", "未作答");
  };
  const incorrectReview = (Array.isArray(result?.review) ? result.review : [])
    .map((item) => ({
      ...item,
      question: questions.find((question) => question.id === item.questionKey),
    }))
    .filter((item) => item.question);
  const currentQuestion = activeQuestions[questionIndex];
  const stateLabel = result
    ? result.passed ? c("Finished", "已完成") : c("Review and retry", "待复习重试")
    : learningComplete ? c("Ready for check", "待练习") : c("Learning", "学习中");

  return (
    <div className="native-learning-flow video-quiz-task">
      <div className="learning-state-row">
        <span className="eyebrow">{c("IN-PLATFORM LEARNING", "站内学习")}</span>
        <span className={`learning-state ${result && !result.passed ? "attention" : ""}`}>
          {stateLabel}
        </span>
      </div>

      {isDocumentTask ? (
        <DocumentLearning
          task={task}
          completed={documentComplete}
          submitting={documentSubmitting}
          error={documentError}
          onComplete={completeDocument}
        />
      ) : (
        <ChapterVideoLearning
          task={task}
          content={content}
          onUpdate={onUpdate}
          onComplete={(elapsed) => onUpdate(task.id, "started", {
            videoProgress: 100,
            videoElapsed: elapsed,
            videoDuration: elapsed,
            videoCompleted: true,
            learningState: "TO_TEST",
            progress: 50,
          })}
        />
      )}

      <section className={`native-quiz-shell ${learningComplete ? "" : "is-locked"}`} aria-disabled={!learningComplete}>
        <header>
          <span><ShieldCheck size={24} weight="duotone" /></span>
          <div>
            <h3>{retryQuestionIds?.length
              ? c(
                  `Retry ${retryQuestionIds.length} incorrect ${retryQuestionIds.length === 1 ? "answer" : "answers"}`,
                  `重做 ${retryQuestionIds.length} 道错题`,
                )
              : task.expectedQuestionCount
              ? c(
                  `${task.expectedQuestionCount}-question check`,
                  `${task.expectedQuestionCount} 道练习题`,
                )
              : c("Knowledge check", "课后练习")}</h3>
            <p>{c(
              `One question at a time · ${content.passScore}% to pass`,
              `每次展示一题 · 正确率达到 ${content.passScore}% 即可通过`,
            )}</p>
          </div>
          {!learningComplete && <Lock size={20} weight="fill" />}
        </header>
        {!learningComplete ? (
          <p className="quiz-locked-copy">{c(
            isDocumentTask
              ? "Read the rules and FAQ, then mark the document as reviewed to open the check."
              : "Finish the first complete video view to open the check.",
            isDocumentTask
              ? "阅读规则与 FAQ 并确认完成后，即可开始练习。"
              : "首次完整看完视频后即可开始练习。",
          )}</p>
        ) : questions.length === 0 ? (
          <div className="quiz-material-pending" role="status">
            <WarningCircle size={22} weight="fill" />
            <span><strong>{c("The check is being prepared", "练习内容准备中")}</strong>{c(
              "The check will appear here when the questions are published.",
              "题目发布后会直接显示在此处，无需跳转到其他页面。",
            )}</span>
          </div>
        ) : result ? (
          <div className={`quiz-result-review ${result.passed ? "is-passed" : ""}`} role="status">
            <span className="result-icon">{result.passed
              ? <CheckCircle size={25} weight="fill" />
              : <Lightbulb size={25} weight="fill" />}</span>
            <h3>{result.passed
              ? c(
                  "You passed. Review your answers below.",
                  "你已通过，可以在下方查看答题结果。",
                )
              : c(
                  "Review the incorrect answers, then try them again.",
                  "这次还未通过，先核对错题，再重做错题。",
                )}</h3>
            <p>{c(
              `Score ${result.score}% · ${result.correct} of ${questions.length} correct`,
              `得分 ${result.score}% · 答对 ${result.correct} / ${questions.length}`,
            )}</p>
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
                      <strong>{index + 1}. {item.question.question}</strong>
                      <span>{c("Review needed", "回答有误")}</span>
                      <p><strong>{c("Your answer: ", "你的答案：")}</strong>{answerText(item.question, item.selectedAnswer)}</p>
                      <p><strong>{c("Correct answer: ", "正确答案：")}</strong>{answerText(item.question, item.correctAnswer)}</p>
                    </article>
                  ))}
                </div>
              </section>
            ) : Number(result.correct) === questions.length ? (
              <div className="all-correct-note"><CheckCircle size={18} weight="fill" />{c("All answers are correct—great work.", "全部答对，做得很好。")}</div>
            ) : (
              <div className="quiz-material-pending" role="status">
                <WarningCircle size={20} weight="fill" />
                <span>{c("The incorrect-answer review is syncing. Refresh this task shortly.", "错题答案正在同步，请稍后刷新任务页查看。")}</span>
              </div>
            )}
            {incorrectReview.length > 0 && (
              <button className="primary-button" type="button" onClick={() => retry(incorrectReview.map((item) => item.questionKey))}>
                <ArrowClockwise size={18} />{c("Retry incorrect answers", "重做错题")}
              </button>
            )}
            {result.passed
              ? <CompletedState task={task} />
              : incorrectReview.length === 0 && (
                <button className="primary-button" type="button" onClick={() => retry()}>
                  <ArrowClockwise size={18} />{c("Retry check", "重新作答")}
                </button>
              )}
          </div>
        ) : (
          <div className="single-question-flow">
            <div className="quiz-progress">
              <span>{c(`Question ${questionIndex + 1} of ${activeQuestions.length}`, `第 ${questionIndex + 1} / ${activeQuestions.length} 题`)}</span>
              <span>{c(`${answeredCount} answered`, `已作答 ${answeredCount} 题`)}</span>
            </div>
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
              {questionIndex < activeQuestions.length - 1
                ? <button className="primary-button" type="button" onClick={() => setQuestionIndex((value) => value + 1)}>{c("Next", "下一题")}<ArrowRight size={17} /></button>
                : <button className="primary-button" type="button" disabled={answeredCount !== activeQuestions.length || submitting} onClick={submitQuiz}>{submitting ? c("Submitting…", "正在提交…") : c("Submit answers", "提交练习")}</button>}
            </div>
            {submitError && <div className="readiness-error" role="alert">{submitError}</div>}
          </div>
        )}
      </section>

      {task.courseModules?.length > 0 && (
        <details className="task-course-details">
          <summary>
            <span>{c("Course key points", "查看课程要点")}</span>
            <strong>{c(`${task.courseModules.length} points`, `${task.courseModules.length} 项`)}</strong>
          </summary>
          <div className="task-course-details-body">
            <ol className="task-course-module-list">
              {task.courseModules.map((module, index) => (
                <li key={`${module.title}-${index}`}><span>{String(index + 1).padStart(2, "0")}</span><div><strong>{language === "zh" ? module.titleZh || module.title : module.title}</strong><p>{language === "zh" ? module.contentZh || module.content : module.content}</p></div></li>
              ))}
            </ol>
          </div>
        </details>
      )}
    </div>
  );
}
