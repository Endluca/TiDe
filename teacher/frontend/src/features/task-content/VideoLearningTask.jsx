import { useEffect, useRef, useState } from "react";
import {
  ArrowsIn,
  ArrowsOut,
  Check,
  CheckCircle,
  Lock,
  Pause,
  Play,
  Sparkle,
  WarningCircle,
} from "@phosphor-icons/react";
import { useI18n } from "../../i18n";
import { Toki } from "../../components/UI";
import "./video-learning-task.css";

const formatTime = (seconds) => {
  if (!Number.isFinite(seconds) || seconds < 0) return "00:00";
  const minutes = Math.floor(seconds / 60);
  const remainder = Math.floor(seconds % 60);
  return `${String(minutes).padStart(2, "0")}:${String(remainder).padStart(2, "0")}`;
};

export function CompletedState({ task, onReplayVideo }) {
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
        {onReplayVideo && (
          <div className="completed-learning-actions">
            <button className="secondary-button" type="button" onClick={onReplayVideo}>
              <Play size={18} weight="fill" />
              {c("Replay video", "回看视频")}
            </button>
          </div>
        )}
      </div>
      <Toki mood="celebrate" motion="celebrate" className="result-toki" />
    </div>
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
      <div className="video-material-pending" role="status">
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
