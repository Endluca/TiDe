import { useEffect, useRef, useState } from "react";
import {
  BookOpen,
  Camera,
  Check,
  CheckCircle,
  ClockCounterClockwise,
  SealCheck,
  ShieldCheck,
  HourglassMedium,
  WarningCircle,
} from "@phosphor-icons/react";
import {
  localizeApiError,
  localizedValidationMessage,
} from "../../api-error-copy";
import { useI18n } from "../../i18n";
import ReadinessExampleGallery from "./ReadinessExampleGallery";
import {
  normalizeReadinessAnalysis,
  readinessPayloadFromValidation,
} from "./readiness-analysis";
import {
  TEACHING_ENVIRONMENT_REFERENCE_PHOTO,
  TEACHING_ENVIRONMENT_STANDARDS,
  teachingEnvironmentCameraErrors,
} from "./teaching-environment-photo";
import "./readiness-photo-task.css";

export default function ReadinessPhotoTask({ task }) {
  const { language } = useI18n();
  const c = (en, zh) => language === "zh" ? zh : en;
  const criteria = TEACHING_ENVIRONMENT_STANDARDS[language]
    || TEACHING_ENVIRONMENT_STANDARDS.en;
  const videoRef = useRef(null);
  const streamRef = useRef(null);
  const photoTakenRef = useRef(Boolean(task.readinessPhoto));
  const finalizingRef = useRef(false);
  const executionReady = Boolean(task.execution?.live);
  const taskCompleted = task.status === "completed";
  const photoStep = task.execution?.findStep("ENVIRONMENT_PHOTO")
    || task.execution?.findStep("UPLOAD");
  const photoProgress = task.execution?.steps?.[photoStep?.stepKey];
  const coursewareStep = task.execution?.findStep("COURSEWARE_CONFIRMATION")
    || task.execution?.findStep("CHECKLIST");
  const coursewareItem = coursewareStep?.config?.items?.[0];
  const coursewareProgress = task.execution?.steps?.[coursewareStep?.stepKey];
  const coursewareRequired = Boolean(coursewareStep);
  const hasCurrentCoursewareEvidence = Boolean(
    coursewareStep?.config?.version
    && coursewareProgress?.details?.checklistVersion === coursewareStep.config.version,
  );
  const hasCurrentTwoPartCompletion = Boolean(
    taskCompleted
    && photoProgress?.status === "COMPLETED"
    && hasCurrentCoursewareEvidence
    && coursewareProgress?.status === "COMPLETED",
  );
  const grandfatheredTaskCompleted = taskCompleted && !hasCurrentTwoPartCompletion;
  const [cameraOpen, setCameraOpen] = useState(false);
  const [cameraReady, setCameraReady] = useState(false);
  const [opening, setOpening] = useState(false);
  const [photo, setPhoto] = useState(task.readinessPhoto || "");
  const [photoFile, setPhotoFile] = useState(null);
  const [photoApproved, setPhotoApproved] = useState(hasCurrentTwoPartCompletion);
  const [reviewChecks, setReviewChecks] = useState(task.readinessChecks || []);
  const [analyzing, setAnalyzing] = useState(false);
  const [coursewareConfirmed, setCoursewareConfirmed] = useState(
    hasCurrentCoursewareEvidence && coursewareProgress?.status === "COMPLETED",
  );
  const [savingCourseware, setSavingCourseware] = useState(false);
  const [photoError, setPhotoError] = useState("");
  const [coursewareError, setCoursewareError] = useState("");
  const [completionError, setCompletionError] = useState("");
  const [finalizing, setFinalizing] = useState(false);

  const stopCamera = () => {
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
    if (videoRef.current) videoRef.current.srcObject = null;
    setCameraOpen(false);
    setCameraReady(false);
  };

  useEffect(() => () => stopCamera(), []);
  useEffect(() => () => {
    if (photo.startsWith("blob:")) URL.revokeObjectURL(photo);
  }, [photo]);
  useEffect(() => {
    setCoursewareConfirmed(
      hasCurrentCoursewareEvidence && coursewareProgress?.status === "COMPLETED",
    );
  }, [coursewareProgress?.status, hasCurrentCoursewareEvidence]);
  useEffect(() => {
    if (hasCurrentTwoPartCompletion) setPhotoApproved(true);
  }, [hasCurrentTwoPartCompletion]);

  const applyValidation = (validation) => {
    const payload = readinessPayloadFromValidation(validation);
    if (!payload) return null;
    const analysis = normalizeReadinessAnalysis(
      payload,
      criteria.map(([id, title, detail]) => ({ id, title, detail })),
      c,
    );
    setReviewChecks(analysis.checks);
    setPhotoApproved(analysis.status === "approved");
    return analysis;
  };

  useEffect(() => {
    if (!executionReady || !task.execution?.loadValidation || taskCompleted) return;
    const controller = new AbortController();
    task.execution.loadValidation(controller.signal)
      .then((validation) => applyValidation(validation))
      .catch(() => undefined);
    return () => controller.abort();
  }, [executionReady, language, task.backendId, taskCompleted]);

  useEffect(() => {
    if (!cameraOpen || !videoRef.current || !streamRef.current) return;
    videoRef.current.srcObject = streamRef.current;
    videoRef.current.play().catch(() => {
      task.execution?.track?.("CAMERA_FAILED", {
        stepKey: "ENVIRONMENT_PHOTO",
        stepType: "CAMERA",
        errorCode: "CAMERA_PREVIEW_FAILED",
        result: "FAILURE",
      });
      setPhotoError(c("The camera preview could not start. Refresh the page and try again.", "摄像头画面无法播放，请刷新页面后重新拍摄。"));
      stopCamera();
    });
  }, [cameraOpen, language]);

  const openCamera = async () => {
    setPhotoError("");
    if (!navigator.mediaDevices?.getUserMedia) {
      task.execution?.track?.("CAMERA_PERMISSION_RESULT", {
        stepKey: "ENVIRONMENT_PHOTO",
        stepType: "CAMERA",
        permission: "UNSUPPORTED",
        result: "FAILURE",
      });
      task.execution?.track?.("CAMERA_FAILED", {
        stepKey: "ENVIRONMENT_PHOTO",
        stepType: "CAMERA",
        errorCode: "CAMERA_UNSUPPORTED",
        result: "FAILURE",
      });
      setPhotoError(c("This browser cannot open the camera. Use the latest Safari, Chrome or Edge.", "当前浏览器不支持网页摄像头，请改用最新版 Safari、Chrome 或 Edge。"));
      return;
    }
    setOpening(true);
    try {
      stopCamera();
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: false,
        video: {
          facingMode: { ideal: "user" },
          width: { ideal: 1920 },
          height: { ideal: 1080 },
          aspectRatio: { ideal: 16 / 9 },
        },
      });
      streamRef.current = stream;
      setCameraOpen(true);
      task.execution?.track?.("CAMERA_PERMISSION_RESULT", {
        stepKey: "ENVIRONMENT_PHOTO",
        stepType: "CAMERA",
        permission: "GRANTED",
        result: "SUCCESS",
      });
      task.execution?.track?.("CAMERA_OPENED", {
        stepKey: "ENVIRONMENT_PHOTO",
        stepType: "CAMERA",
        result: "SUCCESS",
      });
    } catch (cameraError) {
      task.execution?.track?.("CAMERA_PERMISSION_RESULT", {
        stepKey: "ENVIRONMENT_PHOTO",
        stepType: "CAMERA",
        permission: cameraError?.name === "NotAllowedError" ? "DENIED" : "UNAVAILABLE",
        errorCode: cameraError?.name || "CAMERA_OPEN_FAILED",
        result: "FAILURE",
      });
      task.execution?.track?.("CAMERA_FAILED", {
        stepKey: "ENVIRONMENT_PHOTO",
        stepType: "CAMERA",
        errorCode: cameraError?.name || "CAMERA_OPEN_FAILED",
        result: "FAILURE",
      });
      const messages = teachingEnvironmentCameraErrors(c);
      setPhotoError(messages[cameraError?.name] || c("The camera could not start. Check its permission and try again.", "摄像头暂时无法打开，请检查设备权限后重试。"));
    } finally {
      setOpening(false);
    }
  };

  const confirmCourseware = async () => {
    if (taskCompleted || !coursewareRequired || savingCourseware) return;
    if (!executionReady || !task.execution || !coursewareItem?.key) {
      setCoursewareError(c("The preparation checklist is still syncing. Refresh and try again.", "备课确认内容正在同步，请刷新后重试。"));
      return;
    }
    setSavingCourseware(true);
    setCoursewareError("");
    try {
      const response = await task.execution.saveStep(coursewareStep.stepKey, {
        checkedItemKeys: [coursewareItem.key],
      });
      const confirmed = response?.step?.status === "COMPLETED";
      setCoursewareConfirmed(confirmed);
      if (confirmed) {
        await finalizeIfReady({
          nextCoursewareConfirmed: true,
        });
      }
    } catch (caught) {
      setCoursewareError(localizeApiError(
        caught,
        language,
        c("The preparation confirmation could not be saved. Please retry.", "备课确认保存失败，请重试。"),
      ));
    } finally {
      setSavingCourseware(false);
    }
  };

  const capture = async () => {
    const video = videoRef.current;
    if (!video?.videoWidth || !video?.videoHeight) {
      setPhotoError(c("The camera is not ready yet. Wait a moment and try again.", "摄像头画面还没准备好，请稍等一秒再拍摄。"));
      return;
    }
    if (video.videoHeight > video.videoWidth) {
      setPhotoError(c("Rotate your device to landscape before taking the photo.", "请将设备横放，等画面切换为横向后再拍摄。"));
      return;
    }
    const ratio = 16 / 9;
    const sourceRatio = video.videoWidth / video.videoHeight;
    let sx = 0;
    let sy = 0;
    let sw = video.videoWidth;
    let sh = video.videoHeight;
    if (sourceRatio > ratio) {
      sw = sh * ratio;
      sx = (video.videoWidth - sw) / 2;
    } else if (sourceRatio < ratio) {
      sh = sw / ratio;
      sy = (video.videoHeight - sh) / 2;
    }
    const canvas = document.createElement("canvas");
    canvas.width = Math.max(640, Math.min(1920, Math.floor(sw)));
    canvas.height = Math.round(canvas.width * 9 / 16);
    canvas.getContext("2d")?.drawImage(video, sx, sy, sw, sh, 0, 0, canvas.width, canvas.height);
    const blob = await new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", 0.9));
    if (!blob) {
      task.execution?.track?.("CAMERA_FAILED", {
        stepKey: "ENVIRONMENT_PHOTO",
        stepType: "CAMERA",
        errorCode: "PHOTO_GENERATION_FAILED",
        result: "FAILURE",
      });
      setPhotoError(c("The photo could not be generated. Try again.", "照片生成失败，请重新拍摄。"));
      return;
    }
    task.execution?.track?.(
      photoTakenRef.current ? "CAMERA_PHOTO_RETAKEN" : "CAMERA_PHOTO_TAKEN",
      {
        stepKey: "ENVIRONMENT_PHOTO",
        stepType: "CAMERA",
        result: "SUCCESS",
      },
    );
    photoTakenRef.current = true;
    if (photo.startsWith("blob:")) URL.revokeObjectURL(photo);
    const file = new File([blob], `g02-environment-${Date.now()}.jpg`, { type: "image/jpeg" });
    setPhotoApproved(false);
    setReviewChecks([]);
    setPhotoFile(file);
    setPhoto(URL.createObjectURL(blob));
    stopCamera();
  };

  const submit = async () => {
    if (taskCompleted || analyzing || photoApproved) return;
    if (!executionReady || !photoFile || !task.execution) {
      setPhotoError(c("This task is not connected to the execution service.", "当前任务尚未连接执行服务，请稍后重试。"));
      return;
    }
    setAnalyzing(true);
    setPhotoError("");
    try {
      await task.execution.uploadStep("ENVIRONMENT_PHOTO", photoFile);
      const response = await task.execution.submit();
      const persistedValidation = task.execution.loadValidation
        ? await task.execution.loadValidation()
        : response?.validation;
      const analysis = applyValidation(persistedValidation);
      const photoPassed = analysis?.status === "approved"
        || response?.status === "COMPLETED"
        || response?.validation?.status === "PASSED";
      if (photoPassed) {
        setPhotoApproved(true);
        if (coursewareConfirmed && response?.status !== "COMPLETED") {
          const completionValidation = persistedValidation ?? response?.validation;
          setCompletionError(
            completionValidation?.resultCode === "STEPS_INCOMPLETE"
              ? c(
                  "Both parts are saved, but final completion is not confirmed yet. Please retry.",
                  "两部分已经保存，但最终完成状态尚未确认，请重试。",
                )
              : localizedValidationMessage(
                  completionValidation,
                  language,
                  c(
                    "Both parts are saved, but final completion is not confirmed yet. Please retry.",
                    "两部分已经保存，但最终完成状态尚未确认，请重试。",
                  ),
                ),
          );
        }
      } else {
        const validation = persistedValidation ?? response?.validation;
        setPhotoError(analysis?.teacherMessage || (
          validation?.resultCode === "STEPS_INCOMPLETE"
            ? c(
                "The photo check result has not returned yet. Retry this photo later; courseware preparation remains available.",
                "照片检测结果尚未返回，请稍后重试本部分；课件准备仍可正常操作。",
              )
            : localizedValidationMessage(
                validation,
                language,
                c("The photo check did not finish. Please retry this part.", "照片检测未能完成，请重试本部分。"),
              )
        ));
      }
    } catch (caught) {
      setPhotoError(localizeApiError(
        caught,
        language,
        c("The photo could not be submitted. Please retry.", "照片提交失败，请重试。"),
      ));
    } finally {
      setAnalyzing(false);
    }
  };

  const completedChecks = reviewChecks.length > 0
    ? reviewChecks
    : task.readinessChecks || [];
  const exampleFocusId = completedChecks.find((check) => (
    ["fail", "uncertain"].includes(check.status)
  ))?.id || "camera_angle";
  const partStates = [photoApproved, coursewareConfirmed];
  const completedPartCount = partStates.filter(Boolean).length;
  const photoRemainingCount = Math.max(0, 1 - Number(coursewareConfirmed));

  const finalizeIfReady = async ({
    nextPhotoApproved = photoApproved,
    nextCoursewareConfirmed = coursewareConfirmed,
    reportTo,
  } = {}) => {
    if (
      taskCompleted
      || finalizingRef.current
      || !nextPhotoApproved
      || !nextCoursewareConfirmed
    ) return null;
    finalizingRef.current = true;
    setFinalizing(true);
    setCompletionError("");
    try {
      const completion = await task.execution.submit();
      if (completion?.status !== "COMPLETED") {
        const message = completion?.validation?.resultCode === "STEPS_INCOMPLETE"
          ? c("Both parts are saved, but final completion is not confirmed yet. Please retry.", "两部分已经保存，但最终完成状态尚未确认，请重试。")
          : localizedValidationMessage(
              completion?.validation,
              language,
              c("Both parts are saved, but final completion is not confirmed yet. Please retry.", "两部分已经保存，但最终完成状态尚未确认，请重试。"),
            );
        setCompletionError(message);
        reportTo?.(message);
      }
      return completion;
    } catch (caught) {
      const message = localizeApiError(
        caught,
        language,
        c("Final completion failed. Please retry this part.", "任务完成提交失败，请重试当前部分。"),
      );
      setCompletionError(message);
      reportTo?.(message);
      return null;
    } finally {
      finalizingRef.current = false;
      setFinalizing(false);
    }
  };

  useEffect(() => {
    if (
      taskCompleted
      || !photoApproved
      || !coursewareConfirmed
    ) return;
    void finalizeIfReady({ reportTo: setCompletionError });
  }, [coursewareConfirmed, photoApproved, taskCompleted]);

  return (
    <div className="readiness-photo-task g04-readiness-flow">
      <section className="g04-progress-overview" aria-label={c("G04 completion progress", "G04 完成进度")}>
        <div className="g04-progress-heading">
          {grandfatheredTaskCompleted ? (
            <>
              <div>
                <span className="eyebrow">{c("EARLIER VERSION COMPLETION", "早期版本完成记录")}</span>
                <h3>{c("Your completed status is preserved", "已完成状态继续保留")}</h3>
                <p>{c(
                  "This task was completed before the current two-part flow was released. The current cards do not claim individual passes; your final status stays unchanged and no recheck is required.",
                  "该任务完成于当前两模块流程上线前。当前模块不补写单项通过结果；任务终态保持不变，也无需重新操作。",
                )}</p>
              </div>
              <strong className="g04-legacy-complete-badge"><ClockCounterClockwise size={20} weight="duotone" />{c("Completed", "已完成")}</strong>
            </>
          ) : (
            <>
              <div>
                <span className="eyebrow">{c("TWO INDEPENDENT PARTS", "两个独立部分")}</span>
                <h3>{c("Complete both in any order", "两部分可以任意顺序完成")}</h3>
                <p>{c("A problem in one part will not lock the other. G04 completes only after both pass.", "某一部分遇到问题不会锁定另一部分；两部分全部通过后 G04 才会完成。")}</p>
              </div>
              <strong className="g04-progress-count">{completedPartCount}<small>/2</small></strong>
            </>
          )}
        </div>
        {!grandfatheredTaskCompleted && (
          <>
            <div
              className="g04-progress-track"
              role="progressbar"
              aria-label={c("Completed G04 parts", "G04 已完成模块")}
              aria-valuemin="0"
              aria-valuemax="2"
              aria-valuenow={completedPartCount}
              aria-valuetext={c(`${completedPartCount} of 2 parts completed`, `已完成 ${completedPartCount}/2 个模块`)}
            >
              <span style={{ width: `${(completedPartCount / 2) * 100}%` }} />
            </div>
            <ol className="g04-stepper">
              {[
                [c("Photo AI check", "照片 AI 检测"), photoApproved],
                [c("Courseware preparation", "课件准备"), coursewareConfirmed],
              ].map(([label, passed], index) => (
                <li className={passed ? "is-complete" : ""} key={label}>
                  <span>{passed ? <Check size={15} weight="bold" /> : index + 1}</span>
                  <strong>{label}</strong>
                  <small>{passed ? c("Passed", "已通过") : c("To do", "待完成")}</small>
                </li>
              ))}
            </ol>
          </>
        )}
        {completionError && <div className="readiness-error g04-completion-error" role="alert"><WarningCircle size={20} weight="fill" />{completionError}</div>}
        {completedPartCount === 2 && !taskCompleted && (
          <div className="g04-finalize-panel" role="status">
            <SealCheck size={24} weight="duotone" />
            <span><strong>{c("Both parts are saved", "两部分已全部保存")}</strong><small>{c("If completion does not update automatically, retry the final submission here. Both results will stay saved.", "如最终状态没有自动更新，可在此重试完成提交；两部分的已通过结果不会丢失。")}</small></span>
            <button className="secondary-button" type="button" disabled={finalizing} onClick={() => finalizeIfReady({ reportTo: setCompletionError })}>{finalizing ? c("Submitting…", "提交中…") : c("Retry completion", "重试完成提交")}</button>
          </div>
        )}
      </section>

      <div className="g04-part-stack">
        <section className={`g04-part-card g04-photo-part ${grandfatheredTaskCompleted ? "is-legacy" : photoApproved ? "is-complete" : ""}`} data-g04-part="photo">
          <header className="g04-part-header">
            <span className="g04-part-number">01</span>
            <div>
              <small>{c("TEACHING VIEW PHOTO", "授课画面照片")}</small>
              <h3>{c("Take one photo for the four-item AI check", "拍一张照片完成四项 AI 检测")}</h3>
            </div>
            <span className={`g04-part-status ${grandfatheredTaskCompleted ? "is-legacy" : photoApproved ? "is-complete" : ""}`}>
              {grandfatheredTaskCompleted ? <ClockCounterClockwise size={17} /> : photoApproved ? <CheckCircle size={17} weight="fill" /> : <Camera size={17} />}
              {grandfatheredTaskCompleted ? c("Earlier version", "早期版本") : photoApproved ? c("Passed", "已通过") : analyzing ? c("Checking", "检测中") : c("To do", "待完成")}
            </span>
          </header>
          <div className="g04-part-body g04-photo-body">
            <div className="readiness-section-head compact-heading">
              <div>
                <span className="eyebrow">{c("PRE-CLASS ENVIRONMENT", "课前环境确认")}</span>
                <h3>{c("Use the Self-intro camera standard", "按 Self-intro 画面标准拍摄")}</h3>
                <p>{c("This photo checks camera angle, lighting, background and dressing only.", "本照片只检测摄像头角度、光线、背景和着装。")}</p>
              </div>
              <Camera size={28} weight="fill" />
            </div>

            <div className="readiness-guidelines">
              {criteria.map(([id, title, detail], index) => (
                <article key={id}>
                  <span>{String(index + 1).padStart(2, "0")}</span>
                  <div><strong>{title}</strong><p>{detail}</p></div>
                </article>
              ))}
            </div>

            <ReadinessExampleGallery initialActiveId={exampleFocusId} />

            {completedChecks.length > 0 && (
              <div className="readiness-result-list">
                {completedChecks.map((check) => {
                  const status = ["fail", "uncertain"].includes(check.status) ? check.status : "pass";
                  return (
                    <article className={`result-${status}`} key={check.id || check.title}>
                      <span>{status === "pass" ? <Check size={16} weight="bold" /> : <WarningCircle size={16} weight="fill" />}</span>
                      <div><strong>{check.title}</strong>{check.message && <small>{check.message}</small>}{check.suggestion && <p>{check.suggestion}</p>}</div>
                      <em>{status === "pass" ? c("Passed", "通过") : status === "fail" ? c("Adjust", "需调整") : c("Uncertain", "无法判断")}</em>
                    </article>
                  );
                })}
              </div>
            )}

            {!grandfatheredTaskCompleted && photoApproved && (
              <div className="readiness-complete" role="status">
                <SealCheck size={30} weight="fill" />
                <div><strong>{c("The photo check passed", "照片检测已通过")}</strong><p>{taskCompleted
                  ? c("The first qualified photo is saved as completion evidence.", "首次合格照片已作为完成证据保存。")
                  : photoRemainingCount > 0
                    ? c(`${photoRemainingCount} other part${photoRemainingCount > 1 ? "s" : ""} remain. You can complete them independently.`, `总任务还差 ${photoRemainingCount} 项；可继续独立完成。`)
                    : c("Both parts are ready. Final completion is syncing.", "两部分已齐，正在同步最终完成状态。")}</p></div>
              </div>
            )}

            {photoError && <div className="readiness-error" role="alert"><WarningCircle size={20} weight="fill" />{photoError}</div>}

            {grandfatheredTaskCompleted ? (
              <div className="g04-legacy-module-note" role="status">
                <ClockCounterClockwise size={26} weight="duotone" />
                <div><strong>{c("Completed under the earlier photo flow", "已按早期照片流程完成")}</strong><p>{c("No matching result for the current four-item AI check is recorded, so this card does not claim a pass. Your task completion stays unchanged and no new photo is required.", "系统没有当前四项 AI 检测的匹配记录，因此这里不显示“已通过”；任务终态不变，也无需重新拍照。")}</p></div>
              </div>
            ) : task.status === "verifying" && !photoApproved ? (
              <div className="readiness-complete" role="status">
                <HourglassMedium size={30} weight="fill" />
                <div><strong>{c("AI review is in progress", "AI 正在审核")}</strong><p>{c("The photo is saved. Courseware preparation remains available while the result updates.", "照片已保存；结果更新期间，课件准备仍可正常操作。")}</p></div>
              </div>
            ) : photoApproved ? null : (
              <>
                <section className="readiness-capture">
                  {cameraOpen ? (
                    <div className="readiness-live">
                      <div className="readiness-camera-stage">
                        <video ref={videoRef} autoPlay muted playsInline onCanPlay={() => setCameraReady(true)} />
                        <div className="readiness-person-guide" aria-hidden="true">
                          <svg viewBox="0 0 640 360"><ellipse cx="320" cy="105" rx="45" ry="58" /><path d="M190 310 C198 242 236 202 287 187 C296 184 302 177 304 166 M336 166 C338 177 344 184 353 187 C404 202 442 242 450 310" /></svg>
                          <span>{c("Face forward and align with the guide", "保持正脸并贴合辅助线")}</span>
                        </div>
                      </div>
                      <div className="readiness-camera-actions">
                        <button className="primary-button" type="button" disabled={!cameraReady} onClick={capture}><Camera size={18} weight="fill" />{cameraReady ? c("Take photo", "立即拍照") : c("Preparing camera…", "正在准备画面…")}</button>
                        <button className="secondary-button" type="button" onClick={stopCamera}>{c("Cancel", "取消")}</button>
                      </div>
                    </div>
                  ) : photo ? (
                    <div className="readiness-photo-preview">
                      <img src={photo} alt={c("Photo ready for the pre-class check", "待提交的课前准备照片")} />
                      <div className="readiness-person-guide" aria-hidden="true">
                        <svg viewBox="0 0 640 360"><ellipse cx="320" cy="105" rx="45" ry="58" /><path d="M190 310 C198 242 236 202 287 187 C296 184 302 177 304 166 M336 166 C338 177 344 184 353 187 C404 202 442 242 450 310" /></svg>
                        <span>{c("Check: front-facing and aligned", "请确认：正脸且贴合辅助线")}</span>
                      </div>
                      <span><CheckCircle size={18} weight="fill" />{c("Photo ready", "照片已拍摄")}</span>
                      <button className="secondary-button" type="button" onClick={() => { if (photo.startsWith("blob:")) URL.revokeObjectURL(photo); setPhotoApproved(false); setReviewChecks([]); setPhoto(""); setPhotoFile(null); }}>{c("Retake", "重新拍摄")}</button>
                    </div>
                  ) : (
                    <div className="readiness-empty">
                      <div className="readiness-example">
                        <img src={TEACHING_ENVIRONMENT_REFERENCE_PHOTO} alt={c("Qualified Self-intro example", "Self-intro 合格示例")} />
                        <span>{c("Qualified example", "合格示例")}</span>
                      </div>
                      <div className="readiness-empty-copy">
                        <Camera size={38} weight="duotone" />
                        <strong>{c("Take the photo in your real teaching position", "在真实授课位置拍摄")}</strong>
                        <small>{c("Use a clear 16:9 landscape photo. Face forward and align your face and shoulders with the guide.", "使用清晰的 16:9 横向画面；保持正脸，让脸部和肩部贴合辅助线。")}</small>
                        <button className="primary-button" type="button" disabled={opening} onClick={openCamera}><Camera size={18} weight="fill" />{opening ? c("Opening camera…", "正在打开摄像头…") : c("Open camera", "打开相机拍照")}</button>
                      </div>
                    </div>
                  )}
                </section>
                <button className="primary-button wide-button" type="button" disabled={!photoFile || analyzing || photoApproved} onClick={submit}>
                  {photoApproved
                    ? c("Photo check passed", "照片检测已通过")
                    : analyzing
                      ? c("Checking the four items…", "正在检测 4 项画面标准…")
                      : c("Submit photo and start check", "提交照片并开始检测")}
                </button>
                <p className="readiness-retake-note">{c(
                  "You can retake and resubmit until all four items pass. The first qualified photo becomes the completion evidence.",
                  "4 项全部通过前可反复重拍并提交；首次合格照片将作为完成证据。",
                )}</p>
                <p className="readiness-privacy"><ShieldCheck size={17} weight="fill" />{c("Use your real teaching environment and keep private information out of view.", "请使用真实授课环境拍摄，并避免在画面中暴露个人隐私信息。")}</p>
              </>
            )}
          </div>
        </section>

        <section className={`g04-part-card g04-courseware-part ${grandfatheredTaskCompleted ? "is-legacy" : coursewareConfirmed ? "is-complete" : ""}`} data-g04-part="courseware">
          <header className="g04-part-header">
            <span className="g04-part-number">02</span>
            <div>
              <small>{c("LESSON PREPARATION", "首课备课须知")}</small>
              <h3>{c("Review the lesson slides, then confirm preparation", "浏览全部课件后确认备课完成")}</h3>
            </div>
            <span className={`g04-part-status ${grandfatheredTaskCompleted ? "is-legacy" : coursewareConfirmed ? "is-complete" : ""}`}>
              {grandfatheredTaskCompleted ? <ClockCounterClockwise size={17} /> : coursewareConfirmed ? <CheckCircle size={17} weight="fill" /> : <BookOpen size={17} />}
              {grandfatheredTaskCompleted ? c("Earlier version", "早期版本") : coursewareConfirmed ? c("Confirmed", "已确认") : savingCourseware ? c("Saving", "保存中") : c("To do", "待完成")}
            </span>
          </header>
          <div className="g04-part-body">
            <div className="g04-courseware-guidance">
              <BookOpen size={24} weight="duotone" />
              <div><strong>{c("Before you confirm", "确认前请完成")}</strong><p>{c("Review every lesson slide and check the teaching aids, activity flow and timing. This confirmation is separate from the photo check.", "浏览本节课的全部课件，检查教具、课堂环节和时间安排。该确认与照片检测互相独立。")}</p></div>
            </div>
            {coursewareError && <div className="readiness-error" role="alert"><WarningCircle size={20} weight="fill" />{coursewareError}</div>}
            {grandfatheredTaskCompleted ? (
              <div className="g04-legacy-module-note" role="status">
                <ClockCounterClockwise size={26} weight="duotone" />
                <div><strong>{c("Completed under the earlier preparation flow", "已按早期备课流程完成")}</strong><p>{c("No matching confirmation for the current preparation section is recorded. The completed task remains final and no additional confirmation is required.", "系统没有当前备课模块的匹配确认记录；任务完成终态继续保留，无需补做确认。")}</p></div>
              </div>
            ) : coursewareRequired || taskCompleted ? (
              <section className={`readiness-preparation-confirm ${coursewareConfirmed ? "is-confirmed" : ""}`} aria-label={c("Lesson preparation", "首课备课")}>
                <div>
                  <CheckCircle size={24} weight={coursewareConfirmed ? "fill" : "duotone"} />
                  <span>
                    <strong>{c("Confirm lesson preparation", "确认已完成备课")}</strong>
                    <small>{coursewareItem?.[language === "zh" ? "labelZh" : "label"]
                      || coursewareItem?.label
                      || c("I have reviewed all lesson slides and finished preparing.", "我已浏览全部课件，并完成本节课备课。")}</small>
                  </span>
                </div>
                <button
                  className="secondary-button"
                  type="button"
                  disabled={taskCompleted || coursewareConfirmed || savingCourseware}
                  onClick={confirmCourseware}
                >
                  {coursewareConfirmed
                    ? c("Confirmed", "已确认")
                    : savingCourseware
                      ? c("Saving…", "保存中…")
                      : c("Confirm", "确认完成")}
                </button>
              </section>
            ) : (
              <div className="readiness-error" role="alert"><WarningCircle size={20} weight="fill" />{c("The preparation checklist is syncing. You may complete the photo check now.", "备课确认内容正在同步，你可以先完成照片检测。")}</div>
            )}
          </div>
        </section>
      </div>
    </div>
  );
}
