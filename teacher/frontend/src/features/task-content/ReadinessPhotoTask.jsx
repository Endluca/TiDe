import { useEffect, useRef, useState } from "react";
import {
  Camera,
  Check,
  CheckCircle,
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
import { publicAsset } from "../../public-assets";
import ReadinessExampleGallery from "./ReadinessExampleGallery";
import {
  normalizeReadinessAnalysis,
  readinessPayloadFromValidation,
} from "./readiness-analysis";
import "./readiness-photo-task.css";

const CHECKLIST_REFERENCE_PHOTO = publicAsset(
  "/readiness/lesson-preparation-examples/camera-angle-good-front.jpg",
);

const standards = {
  en: [
    ["camera_angle", "Camera angle", "Face the camera directly and keep your head upright. Align your face and shoulders with the guide, with the camera at eye level. Side profiles or visibly turned, tilted, raised or lowered heads will not pass."],
    ["lighting", "Lighting", "Keep your face evenly and brightly lit. It should not be too dark, overexposed or strongly backlit."],
    ["background", "Background", "Use a clean, appropriate background without distractions. A virtual background must display clearly without covering your face or body."],
    ["dressing", "Dressing", "Wear neat, professional clothing that is suitable for teaching young learners online."],
  ],
  zh: [
    ["camera_angle", "摄像头角度", "必须正脸面对摄像头并保持头部端正，脸部和肩部尽量贴合辅助线，摄像头与视线平齐；侧脸、明显转头、仰头、低头或头部侧倾不通过。"],
    ["lighting", "光线", "面部光线均匀、明亮，不能过暗、过曝或有明显逆光。"],
    ["background", "背景", "背景干净、合适且不分散注意力；使用虚拟背景时须显示清晰，不遮挡面部或身体。"],
    ["dressing", "着装", "穿着整洁、专业，并适合给少儿进行线上授课。"],
  ],
};

const cameraErrors = (c) => ({
  NotAllowedError: c("Camera permission was not granted. Allow camera access in the browser site settings, then try again.", "未获得摄像头权限。请在浏览器地址栏的站点设置中允许使用摄像头后重试。"),
  NotFoundError: c("No camera was found. Check that a camera is available, then try again.", "没有检测到可用摄像头，请确认设备摄像头可用后重试。"),
  NotReadableError: c("The camera is being used by another app. Close that app, then try again.", "摄像头正被其他程序占用，请关闭占用程序后重试。"),
});

export default function ReadinessPhotoTask({ task }) {
  const { language } = useI18n();
  const c = (en, zh) => language === "zh" ? zh : en;
  const criteria = standards[language] || standards.en;
  const videoRef = useRef(null);
  const streamRef = useRef(null);
  const photoTakenRef = useRef(Boolean(task.readinessPhoto));
  const [cameraOpen, setCameraOpen] = useState(false);
  const [cameraReady, setCameraReady] = useState(false);
  const [opening, setOpening] = useState(false);
  const [photo, setPhoto] = useState(task.readinessPhoto || "");
  const [photoFile, setPhotoFile] = useState(null);
  const [photoApproved, setPhotoApproved] = useState(task.status === "completed");
  const [reviewChecks, setReviewChecks] = useState(task.readinessChecks || []);
  const [analyzing, setAnalyzing] = useState(false);
  const executionReady = Boolean(task.execution?.live);
  const taskCompleted = task.status === "completed";
  const coursewareStep = task.execution?.findStep("COURSEWARE_CONFIRMATION")
    || task.execution?.findStep("CHECKLIST");
  const coursewareItem = coursewareStep?.config?.items?.[0];
  const coursewareProgress = task.execution?.steps?.[coursewareStep?.stepKey];
  const coursewareRequired = Boolean(coursewareStep);
  const [coursewareConfirmed, setCoursewareConfirmed] = useState(
    taskCompleted || !coursewareRequired || coursewareProgress?.status === "COMPLETED",
  );
  const [savingCourseware, setSavingCourseware] = useState(false);
  const [error, setError] = useState("");

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
      taskCompleted || !coursewareRequired || coursewareProgress?.status === "COMPLETED",
    );
  }, [coursewareProgress?.status, coursewareRequired, taskCompleted]);
  useEffect(() => {
    if (taskCompleted) setPhotoApproved(true);
  }, [taskCompleted]);

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
      setError(c("The camera preview could not start. Refresh the page and try again.", "摄像头画面无法播放，请刷新页面后重新拍摄。"));
      stopCamera();
    });
  }, [cameraOpen, language]);

  const openCamera = async () => {
    setError("");
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
      setError(c("This browser cannot open the camera. Use the latest Safari, Chrome or Edge.", "当前浏览器不支持网页摄像头，请改用最新版 Safari、Chrome 或 Edge。"));
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
      const messages = cameraErrors(c);
      setError(messages[cameraError?.name] || c("The camera could not start. Check its permission and try again.", "摄像头暂时无法打开，请检查设备权限后重试。"));
    } finally {
      setOpening(false);
    }
  };

  const confirmCourseware = async () => {
    if (!coursewareRequired || savingCourseware) return;
    if (!executionReady || !task.execution || !coursewareItem?.key) {
      setError(c("The preparation checklist is still syncing. Refresh and try again.", "备课确认内容正在同步，请刷新后重试。"));
      return;
    }
    setSavingCourseware(true);
    setError("");
    try {
      const response = await task.execution.saveStep(coursewareStep.stepKey, {
        checkedItemKeys: [coursewareItem.key],
      });
      const confirmed = response?.step?.status === "COMPLETED";
      setCoursewareConfirmed(confirmed);
      if (confirmed && photoApproved) {
        const completion = await task.execution.submit();
        if (completion?.status === "FAILED") {
          setError(localizedValidationMessage(
            completion.validation,
            language,
            c("The task could not be completed. Please retry.", "任务完成失败，请重试。"),
          ));
        }
      }
    } catch (caught) {
      setError(localizeApiError(
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
      setError(c("The camera is not ready yet. Wait a moment and try again.", "摄像头画面还没准备好，请稍等一秒再拍摄。"));
      return;
    }
    if (video.videoHeight > video.videoWidth) {
      setError(c("Rotate your device to landscape before taking the photo.", "请将设备横放，等画面切换为横向后再拍摄。"));
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
      setError(c("The photo could not be generated. Try again.", "照片生成失败，请重新拍摄。"));
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
    if (analyzing || photoApproved) return;
    if (!executionReady || !photoFile || !task.execution) {
      setError(c("This task is not connected to the execution service.", "当前任务尚未连接执行服务，请稍后重试。"));
      return;
    }
    setAnalyzing(true);
    setError("");
    try {
      await task.execution.uploadStep("ENVIRONMENT_PHOTO", photoFile);
      const response = await task.execution.submit();
      const persistedValidation = task.execution.loadValidation
        ? await task.execution.loadValidation()
        : response?.validation;
      const analysis = applyValidation(persistedValidation);
      const photoPassed = analysis?.status === "approved"
        || response?.status === "COMPLETED"
        || response?.validation?.status === "PASSED"
        || response?.validation?.resultCode === "STEPS_INCOMPLETE";
      if (photoPassed) {
        setPhotoApproved(true);
      } else if (response?.status === "FAILED") {
        setError(localizedValidationMessage(
          response.validation,
          language,
          c("Adjust the photo and try again.", "请按提示调整照片后重试。"),
        ));
      }
    } catch (caught) {
      setError(localizeApiError(
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

  return (
    <div className="readiness-photo-task">
      <div className="readiness-section-head">
        <div>
          <span className="eyebrow">{c("PRE-CLASS ENVIRONMENT", "课前环境确认")}</span>
          <h3>{c("Take one photo using the Self-intro standard", "按 Self-intro 标准拍一张照片")}</h3>
          <p>{c("The same photo checks all four items from the lesson-preparation checklist.", "同一张照片会按首课准备清单检测以下 4 项画面标准。")}</p>
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

      {photoApproved && !taskCompleted && (
        <div className="readiness-complete" role="status">
          <SealCheck size={30} weight="fill" />
          <div><strong>{c("The photo check passed", "照片检测已通过")}</strong><p>{c("The photo workflow is complete. Lesson preparation confirmation does not affect this result.", "拍照检测流程已完成，底部备课确认不会影响本次检测结果。")}</p></div>
        </div>
      )}

      {error && <div className="readiness-error" role="alert"><WarningCircle size={20} weight="fill" />{error}</div>}

      {taskCompleted ? (
        <div className="readiness-complete" role="status">
          <SealCheck size={30} weight="fill" />
          <div><strong>{c("Your pre-class teaching view passed", "课前授课画面已通过")}</strong><p>{c("Your first qualified photo and its result are recorded as the completion evidence and cannot be replaced by a later photo.", "首次合格照片及检测结果已记录为完成证据，后续照片不会覆盖。")}</p></div>
        </div>
      ) : task.status === "verifying" ? (
        <div className="readiness-complete" role="status">
          <HourglassMedium size={30} weight="fill" />
          <div><strong>{c("AI review is in progress", "AI 正在审核")}</strong><p>{c("The photo is saved. No repeated submission is needed while the result is updating.", "照片已经保存，结果更新期间无需重复提交。")}</p></div>
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
                  <img src={CHECKLIST_REFERENCE_PHOTO} alt={c("Qualified Self-intro example", "Self-intro 合格示例")} />
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
      {coursewareRequired && (
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
            disabled={coursewareConfirmed || savingCourseware}
            onClick={confirmCourseware}
          >
            {coursewareConfirmed
              ? c("Confirmed", "已确认")
              : savingCourseware
                ? c("Saving…", "保存中…")
                : c("Confirm", "确认完成")}
          </button>
        </section>
      )}
    </div>
  );
}
