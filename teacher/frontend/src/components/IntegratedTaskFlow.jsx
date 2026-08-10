import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import "../task-flows.css";
import { WarningCircle } from "@phosphor-icons/react";
import { uploadTaskFile } from "../api/file-api";
import {
  getTaskValidation,
  retryTask,
  saveTaskProgress,
  saveVideoHeartbeat,
  startTask,
  submitTask,
} from "../api/task-api";
import { localizeApiError } from "../api-error-copy";
import { localizeTask, useI18n } from "../i18n";
import { taskNeedsStart } from "../task-status";
import {
  trackProductEvent,
  trackProductEventOnce,
} from "../analytics/product-analytics";
import TaskFlow from "./TaskFlow";

const uiStatus = {
  ASSIGNED: "available",
  VIEWED: "available",
  IN_PROGRESS: "started",
  SUBMITTED: "submitted",
  UNDER_REVIEW: "verifying",
  COMPLETED: "completed",
  FAILED: "retry_required",
  EXPIRED: "expired",
  WAIVED: "waived",
  CANCELLED: "cancelled",
};

function progressMap(context) {
  return Object.fromEntries((context?.progress?.steps || []).map((step) => [step.stepKey, step]));
}

function stepItems(step) {
  return (step?.config?.items || []).map((item) => (
    typeof item === "string" ? { key: item, label: item } : item
  ));
}

function hydratedTask(task, context, status, steps) {
  const next = {
    ...task,
    backendContext: context,
    backendStatus: status,
    status: uiStatus[status] || task.status,
    backendProgressByStep: steps,
    progress: context?.progress?.percent ?? task.progress,
  };
  const checklist = context?.steps?.find((step) => step.type === "CHECKLIST");
  if (checklist) {
    const checked = new Set(steps[checklist.stepKey]?.details?.checkedItemKeys || []);
    next.checklistProgress = stepItems(checklist)
      .map((item, index) => checked.has(item.key) ? index : null)
      .filter((value) => value !== null);
  }
  const videoSteps = context?.steps?.filter((step) => step.type === "VIDEO") || [];
  if (videoSteps.length === 1) {
    const video = videoSteps[0];
    const progress = steps[video.stepKey];
    next.videoProgress = progress?.percent || 0;
    next.videoElapsed = progress?.details?.resumeSeconds || 0;
    next.videoDuration = Number(video.config?.durationSeconds) || 0;
    next.videoCompleted = progress?.status === "COMPLETED";
  } else if (videoSteps.length > 1) {
    next.chapterVideoState = Object.fromEntries(videoSteps.map((video) => {
      const progress = steps[video.stepKey];
      const chapterId = video.config?.chapterId || video.stepKey;
      return [chapterId, {
        videoProgress: progress?.percent || 0,
        videoElapsed: progress?.details?.resumeSeconds || 0,
        videoDuration: Number(video.config?.durationSeconds) || 0,
        videoCompleted: progress?.status === "COMPLETED",
      }];
    }));
    next.videoProgress = Math.round(videoSteps.reduce((sum, video) => sum + (steps[video.stepKey]?.percent || 0), 0) / videoSteps.length);
    next.videoCompleted = videoSteps.every((video) => steps[video.stepKey]?.status === "COMPLETED");
  }
  const documentStep = context?.steps?.find((step) => step.type === "DOCUMENT");
  if (documentStep) {
    next.documentCompleted = steps[documentStep.stepKey]?.status === "COMPLETED";
  }
  const factualResponse = context?.steps?.find((step) => (
    step.type === "CUSTOM" && step.config?.kind === "TEXT_SUBMISSION"
  ));
  if (factualResponse) {
    next.factualResponse = steps[factualResponse.stepKey]?.details?.text || "";
    next.responseConfig = {
      minCharacters: Number(factualResponse.config?.minCharacters) || 20,
      maxCharacters: Number(factualResponse.config?.maxCharacters) || 2000,
    };
  }
  if (task.taskCode === "G04") {
    const checklistStep = context.steps.find((step) => step.config?.role === "COURSEWARE_CONFIRMATION")
      || context.steps.find((step) => step.type === "CHECKLIST");
    const checked = new Set(steps[checklistStep?.stepKey]?.details?.checkedItemKeys || []);
    const coursewarePrepared = stepItems(checklistStep).some((item) => checked.has(item.key));
    next.coursewarePrepared = coursewarePrepared;
    next.environmentChecks = coursewarePrepared ? [0] : [];
  }
  return next;
}

export default function IntegratedTaskFlow({
  task,
  onRefresh,
  onTaskSubmitted,
  onHelp,
  onKuozhiProgressStateChange,
}) {
  const { language } = useI18n();
  const context = task.backendContext;
  const [stateVersion, setStateVersion] = useState(context?.stateVersion || task.stateVersion);
  const [status, setStatus] = useState(context?.status || task.backendStatus);
  const [steps, setSteps] = useState(() => progressMap(context));
  const [presentationPatch, setPresentationPatch] = useState({});
  const [error, setError] = useState("");
  const stateVersionRef = useRef(stateVersion);
  const statusRef = useRef(status);
  const queueRef = useRef(Promise.resolve());
  const analyticsStartedStepsRef = useRef(new Set());
  const uploadAttemptsRef = useRef(new Map());

  useEffect(() => {
    const nextVersion = context?.stateVersion || task.stateVersion;
    const nextStatus = context?.status || task.backendStatus;
    setStateVersion(nextVersion);
    setStatus(nextStatus);
    setSteps(progressMap(context));
    stateVersionRef.current = nextVersion;
    statusRef.current = nextStatus;
    setPresentationPatch({});
    setError("");
  }, [context, task.backendStatus, task.stateVersion]);

  const updateTaskState = useCallback((response) => {
    if (typeof response?.stateVersion === "number") {
      setStateVersion(response.stateVersion);
      stateVersionRef.current = response.stateVersion;
    }
    if (response?.status) {
      setStatus(response.status);
      statusRef.current = response.status;
    }
    if (response?.step) {
      setSteps((current) => ({
        ...current,
        [response.step.stepKey]: {
          ...(current[response.step.stepKey] || {}),
          stepKey: response.step.stepKey,
          status: response.step.status,
          percent: response.step.percent,
          details: response.step.details || current[response.step.stepKey]?.details || {},
          result: response.step.result,
        },
      }));
    }
    return response;
  }, []);

  const applyLatestContext = useCallback((latest) => {
    if (!latest) return;
    setStateVersion(latest.stateVersion);
    setStatus(latest.status);
    setSteps(progressMap(latest));
    stateVersionRef.current = latest.stateVersion;
    statusRef.current = latest.status;
  }, []);

  const refreshLatestContext = useCallback(async () => {
    const latest = await onRefresh?.(task.backendId);
    applyLatestContext(latest);
    return latest;
  }, [applyLatestContext, onRefresh, task.backendId]);

  const enqueue = useCallback((operation) => {
    const next = queueRef.current.then(operation, operation);
    queueRef.current = next.catch(() => undefined);
    return next;
  }, []);

  const reportError = useCallback((caught) => {
    setError(localizeApiError(
      caught,
      language,
      language === "zh" ? "操作失败，请重试。" : "The action failed. Please retry.",
    ));
  }, [language]);

  const findStep = useCallback((selector, occurrence = 0) => {
    const candidates = context?.steps || [];
    if (!selector) return null;
    const matches = candidates.filter((step) => (
      step.stepKey === selector
      || step.config?.role === selector
      || step.type === selector
    ));
    return matches[occurrence] || null;
  }, [context]);

  const trackStepStart = useCallback((step) => {
    if (!step || analyticsStartedStepsRef.current.has(step.stepKey)) return;
    analyticsStartedStepsRef.current.add(step.stepKey);
    const properties = {
      stepKey: step.stepKey,
      stepType: step.type,
      result: "STARTED",
    };
    trackProductEventOnce(
      "TASK_STEP_STARTED",
      { task, properties },
      step.stepKey,
    );
    const specializedEvent = {
      DOCUMENT: "DOCUMENT_READING_STARTED",
      CHECKLIST: "CHECKLIST_STARTED",
    }[step.type];
    if (specializedEvent) {
      trackProductEventOnce(
        specializedEvent,
        { task, properties },
        step.stepKey,
      );
    }
  }, [task]);

  const ensureStarted = useCallback(async () => {
    const transition = async (currentStatus, currentVersion) => {
      if (currentStatus === "FAILED") {
        return retryTask(task.backendId, currentVersion, "TEACHER_RETRY");
      }
      if (taskNeedsStart(currentStatus)) {
        return startTask(task.backendId, currentVersion);
      }
      return null;
    };

    try {
      const response = await transition(statusRef.current, stateVersionRef.current);
      if (!response) return stateVersionRef.current;
      updateTaskState(response);
      await refreshLatestContext().catch(() => undefined);
      return response.stateVersion;
    } catch (caught) {
      if (caught?.code !== "STATE_VERSION_CONFLICT") throw caught;
      const latest = await refreshLatestContext();
      if (!latest) throw caught;
      if (latest.status === "IN_PROGRESS") return latest.stateVersion;
      const response = await transition(latest.status, latest.stateVersion);
      if (!response) throw caught;
      updateTaskState(response);
      await refreshLatestContext().catch(() => undefined);
      return response.stateVersion;
    }
  }, [refreshLatestContext, task.backendId, updateTaskState]);

  const saveStep = useCallback((selector, progress, occurrence = 0) => enqueue(async () => {
    setError("");
    const step = findStep(selector, occurrence);
    if (!step) throw new Error(language === "zh" ? "任务内容正在同步，请稍后刷新重试。" : "The task content is syncing. Refresh and try again shortly.");
    trackStepStart(step);
    try {
      const version = await ensureStarted();
      const response = await saveTaskProgress(task.backendId, version, step.stepKey, 0, progress);
      updateTaskState(response);
      if (step.type === "DOCUMENT" && response?.step?.status === "COMPLETED") {
        trackProductEventOnce(
          "DOCUMENT_COMPLETED",
          {
            task,
            properties: {
              stepKey: step.stepKey,
              stepType: step.type,
              contentType: "DOCUMENT",
              result: "SUCCESS",
            },
          },
          step.stepKey,
        );
      } else if (step.type === "CHECKLIST") {
        const completedCount = Array.isArray(progress?.checkedItemKeys)
          ? progress.checkedItemKeys.length
          : undefined;
        trackProductEvent("CHECKLIST_PROGRESS_UPDATED", {
          task,
          properties: {
            stepKey: step.stepKey,
            stepType: step.type,
            completedCount,
            result: response?.step?.status || "IN_PROGRESS",
          },
        });
        if (response?.step?.status === "COMPLETED") {
          trackProductEvent("CHECKLIST_SUBMITTED", {
            task,
            properties: {
              stepKey: step.stepKey,
              stepType: step.type,
              completedCount,
              result: "SUCCESS",
            },
          });
        }
      }
      return response;
    } catch (caught) {
      reportError(caught);
      throw caught;
    }
  }), [enqueue, ensureStarted, findStep, language, reportError, task, trackStepStart, updateTaskState]);

  const saveVideo = useCallback((selector, positionSeconds, occurrence = 0) => enqueue(async () => {
    setError("");
    const step = findStep(selector, occurrence);
    if (!step) throw new Error(language === "zh" ? "视频步骤尚未配置。" : "The video step is not configured.");
    try {
      await ensureStarted();
      const response = await saveVideoHeartbeat(task.backendId, step.stepKey, positionSeconds, 1);
      return updateTaskState(response);
    } catch (caught) {
      reportError(caught);
      throw caught;
    }
  }), [enqueue, ensureStarted, findStep, language, reportError, task.backendId, updateTaskState]);

  const uploadStep = useCallback((selector, file, occurrence = 0) => enqueue(async () => {
    setError("");
    const step = findStep(selector, occurrence);
    if (!step) throw new Error(language === "zh" ? "上传步骤尚未配置。" : "The upload step is not configured.");
    trackStepStart(step);
    const attempt = (uploadAttemptsRef.current.get(step.stepKey) || 0) + 1;
    uploadAttemptsRef.current.set(step.stepKey, attempt);
    const startedAt = performance.now();
    trackProductEvent(attempt > 1 ? "UPLOAD_RETRIED" : "UPLOAD_STARTED", {
      task,
      properties: {
        stepKey: step.stepKey,
        stepType: step.type,
        retryNo: attempt,
        contentType: file?.type || "UNKNOWN",
        result: "STARTED",
      },
    });
    try {
      const version = await ensureStarted();
      const ready = await uploadTaskFile({ taskInstanceId: task.backendId, stepKey: step.stepKey, file });
      const response = await saveTaskProgress(task.backendId, version, step.stepKey, 0, { fileId: ready.fileId });
      updateTaskState(response);
      trackProductEvent("UPLOAD_SUCCEEDED", {
        task,
        properties: {
          stepKey: step.stepKey,
          stepType: step.type,
          retryNo: attempt,
          contentType: file?.type || "UNKNOWN",
          durationMs: Math.round(performance.now() - startedAt),
          result: "SUCCESS",
        },
      });
      return { fileId: ready.fileId, response };
    } catch (caught) {
      trackProductEvent("UPLOAD_FAILED", {
        task,
        properties: {
          stepKey: step.stepKey,
          stepType: step.type,
          retryNo: attempt,
          contentType: file?.type || "UNKNOWN",
          durationMs: Math.round(performance.now() - startedAt),
          errorCode: caught?.code || caught?.name || "UPLOAD_FAILED",
          result: "FAILURE",
        },
      });
      reportError(caught);
      throw caught;
    }
  }), [enqueue, ensureStarted, findStep, language, reportError, task, trackStepStart, updateTaskState]);

  const finalize = useCallback(() => enqueue(async () => {
    setError("");
    try {
      const version = await ensureStarted();
      const response = await submitTask(task.backendId, version, []);
      updateTaskState(response);
      try {
        await onRefresh?.(task.backendId);
      } finally {
        try {
          await onTaskSubmitted?.(response);
        } catch {
          // 消息刷新失败不应把已经成功的任务提交回滚为失败。
        }
      }
      return response;
    } catch (caught) {
      reportError(caught);
      throw caught;
    }
  }), [enqueue, ensureStarted, onRefresh, onTaskSubmitted, reportError, task.backendId, updateTaskState]);

  const retry = useCallback(() => enqueue(async () => {
    setError("");
    try {
      const response = await retryTask(task.backendId, stateVersionRef.current, "TEACHER_RETRY");
      updateTaskState(response);
      await onRefresh?.(task.backendId);
      return response;
    } catch (caught) {
      reportError(caught);
      throw caught;
    }
  }), [enqueue, onRefresh, reportError, task.backendId, updateTaskState]);

  const execution = useMemo(() => ({
    live: Boolean(context && task.backendId),
    context,
    steps,
    findStep,
    start: ensureStarted,
    refresh: refreshLatestContext,
    loadValidation: (signal) => getTaskValidation(task.backendId, signal),
    saveStep,
    saveVideo,
    uploadStep,
    submit: finalize,
    retry,
    reportError,
    track: (eventName, properties = {}) => trackProductEvent(eventName, {
      task,
      properties,
    }),
    trackOnce: (eventName, uniqueKey, properties = {}) => trackProductEventOnce(
      eventName,
      { task, properties },
      uniqueKey,
    ),
  }), [context, ensureStarted, finalize, findStep, refreshLatestContext, reportError, retry, saveStep, saveVideo, steps, task, uploadStep]);

  const presentationTask = useMemo(() => ({
    ...localizeTask(hydratedTask(task, context, status, steps), language),
    ...presentationPatch,
    execution,
  }), [context, execution, language, presentationPatch, status, steps, task]);

  const legacyUpdate = useCallback((_taskId, nextStatus, patch = {}) => {
    if (
      typeof patch.factualResponse === "string"
      && context
      && task.backendId
    ) {
      const responseStep = findStep("FACTUAL_RESPONSE") || findStep("CUSTOM");
      return saveStep(responseStep?.stepKey, { text: patch.factualResponse })
        .then((response) => (
          ["submitted", "verifying", "completed"].includes(nextStatus)
            ? finalize()
            : response
        ));
    }
    setPresentationPatch((current) => ({ ...current, ...patch, status: nextStatus }));
    if (!context || !task.backendId) return Promise.resolve(null);
    if (typeof patch.coursewarePrepared === "boolean") {
      const checklist = findStep("COURSEWARE_CONFIRMATION") || findStep("CHECKLIST");
      const keys = patch.coursewarePrepared
        ? stepItems(checklist).slice(0, 1).map((item) => item.key)
        : [];
      return saveStep(checklist?.stepKey, { checkedItemKeys: keys })
        .then((response) => nextStatus === "completed" ? finalize() : response);
    }
    if (patch.checklistProgress) {
      const checklist = findStep("CHECKLIST");
      const keys = stepItems(checklist)
        .filter((_item, index) => patch.checklistProgress.includes(index))
        .map((item) => item.key);
      return saveStep(checklist?.stepKey, { checkedItemKeys: keys })
        .then((response) => nextStatus === "completed" ? finalize() : response);
    }
    if (nextStatus === "completed") return finalize();
    return Promise.resolve(null);
  }, [context, finalize, findStep, saveStep, task.backendId]);

  return (
    <>
      <TaskFlow
        task={presentationTask}
        onUpdate={legacyUpdate}
        onHelp={onHelp}
        onKuozhiProgressStateChange={onKuozhiProgressStateChange}
      />
      {error && presentationTask.taskCode !== "G04" && (
        <div className="auth-form-error" role="alert">
          <WarningCircle size={18} weight="fill" />{error}
        </div>
      )}
    </>
  );
}
