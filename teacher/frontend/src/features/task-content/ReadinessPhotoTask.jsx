import { useEffect, useRef, useState } from "react";
import {
  Camera,
  Check,
  CheckCircle,
  SealCheck,
  ShieldCheck,
  Monitor,
  HourglassMedium,
  WarningCircle,
} from "@phosphor-icons/react";
import {
  localizeApiError,
  localizedValidationMessage,
} from "../../api-error-copy";
import { useI18n } from "../../i18n";
import { publicAsset } from "../../public-assets";
import "./readiness-photo-task.css";

const SELF_INTRO_REFERENCE_PHOTO = publicAsset("/readiness/self-intro-reference-51talk.webp");

const standards = {
  en: [
    ["Clear lighting", "Both sides of your face and facial features must be clearly visible in even front light. Strong backlight, darkness, heavy shadows or overexposure do not pass."],
    ["Complete framing", "Use a 16:9 landscape frame with exactly one teacher. Keep your full unobstructed face centered, leave a little space above your head and show your upper body from the chest up."],
    ["Suitable camera position and presence", "Keep the camera level with your eyes, look toward it and maintain a natural, positive teaching presence."],
    ["Teaching headset worn", "Wear a teaching headset with its microphone clearly visible. Single-ear and double-ear headsets are both acceptable."],
    ["Class-appropriate clothing", "Wear clean, presentable clothing suitable for a formal online class."],
    ["Tidy, stable background", "Keep the view free of obvious clutter, unrelated people or animals, and sensitive personal information."],
    ["Clear, detectable photo", "Use your real teaching environment. Any serious blur, facial obstruction, masking filter, screenshot artifact or compression distortion requires a retake."],
  ],
  zh: [
    ["光线清楚", "面部两侧与五官必须清楚可辨，正面光线均匀；明显逆光、过暗、重阴影或过曝不通过。"],
    ["人物完整入镜", "使用 16:9 横向画面，只保留一位老师；面部无遮挡且完整，人物居中，头顶适当留白，完整拍到胸部以上。"],
    ["机位与状态合适", "镜头与视线平齐，看向镜头，保持自然、积极的授课状态与表情。"],
    ["佩戴授课耳麦", "清楚佩戴带麦克风的授课耳麦，话筒在画面中可见；单耳或双耳均可。"],
    ["着装适合授课", "保持整洁、得体、适合正式线上课堂。"],
    ["背景整洁稳定", "画面无明显杂乱、无关人员或动物，不暴露敏感个人信息。"],
    ["照片清晰可检测", "使用真实授课环境拍摄；严重模糊、面部遮挡、遮盖五官的滤镜、截图痕迹或压缩失真都需要重拍。"],
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
  const [analyzing, setAnalyzing] = useState(false);
  const [checkingDevice, setCheckingDevice] = useState(false);
  const executionReady = Boolean(task.execution?.live);
  const deviceStep = task.execution?.findStep("DEVICE_CHECK");
  const deviceProgress = task.execution?.steps?.[deviceStep?.stepKey];
  const devicePassed = deviceProgress?.status === "COMPLETED";
  const coursewareStep = task.execution?.findStep("COURSEWARE_CONFIRMATION")
    || task.execution?.findStep("CHECKLIST");
  const coursewareItem = coursewareStep?.config?.items?.[0];
  const coursewareProgress = task.execution?.steps?.[coursewareStep?.stepKey];
  const coursewareRequired = Boolean(coursewareStep);
  const [coursewareConfirmed, setCoursewareConfirmed] = useState(
    task.status === "completed" || !coursewareRequired || coursewareProgress?.status === "COMPLETED",
  );
  const [savingCourseware, setSavingCourseware] = useState(false);
  const [deviceResults, setDeviceResults] = useState(deviceProgress?.details?.results || {});
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
      task.status === "completed" || !coursewareRequired || coursewareProgress?.status === "COMPLETED",
    );
  }, [coursewareProgress?.status, coursewareRequired, task.status]);

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

  const runDeviceCheck = async () => {
    if (checkingDevice) return;
    setCheckingDevice(true);
    setError("");
    if (!executionReady || !task.execution) {
      setError(c("This task is not connected to the execution service.", "当前任务尚未连接执行服务，请稍后重试。"));
      setCheckingDevice(false);
      return;
    }
    const results = {
      camera: "FAILED",
      microphone: "FAILED",
      network: navigator.onLine ? "PASSED" : "FAILED",
    };
    try {
      if (navigator.mediaDevices?.getUserMedia) {
        const stream = await navigator.mediaDevices.getUserMedia({ video: true, audio: true });
        task.execution?.track?.("CAMERA_PERMISSION_RESULT", {
          stepKey: deviceStep?.stepKey || "DEVICE_CHECK",
          stepType: "DEVICE_CHECK",
          permission: "GRANTED",
          result: "SUCCESS",
        });
        results.camera = stream.getVideoTracks().length > 0 ? "PASSED" : "FAILED";
        results.microphone = stream.getAudioTracks().length > 0 ? "PASSED" : "FAILED";
        stream.getTracks().forEach((track) => track.stop());
      } else {
        task.execution?.track?.("CAMERA_PERMISSION_RESULT", {
          stepKey: deviceStep?.stepKey || "DEVICE_CHECK",
          stepType: "DEVICE_CHECK",
          permission: "UNSUPPORTED",
          result: "FAILURE",
        });
      }
      const response = await task.execution.saveStep("DEVICE_CHECK", { results });
      setDeviceResults(results);
      if (response?.step?.status !== "COMPLETED") {
        setError(c("Check the device permission and network, then try again.", "请检查摄像头、麦克风权限和网络后重试。"));
      }
    } catch (caught) {
      task.execution?.track?.("CAMERA_PERMISSION_RESULT", {
        stepKey: deviceStep?.stepKey || "DEVICE_CHECK",
        stepType: "DEVICE_CHECK",
        permission: caught?.name === "NotAllowedError" ? "DENIED" : "UNAVAILABLE",
        errorCode: caught?.name || caught?.code || "DEVICE_CHECK_FAILED",
        result: "FAILURE",
      });
      task.execution?.track?.("CAMERA_FAILED", {
        stepKey: deviceStep?.stepKey || "DEVICE_CHECK",
        stepType: "DEVICE_CHECK",
        errorCode: caught?.name || caught?.code || "DEVICE_CHECK_FAILED",
        result: "FAILURE",
      });
      setError(localizeApiError(
        caught,
        language,
        c("The device check failed. Please retry.", "设备检测失败，请重试。"),
      ));
    } finally {
      setCheckingDevice(false);
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
      setCoursewareConfirmed(response?.step?.status === "COMPLETED");
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
    setPhotoFile(file);
    setPhoto(URL.createObjectURL(blob));
    stopCamera();
  };

  const submit = async () => {
    if (analyzing || !devicePassed || !coursewareConfirmed) return;
    if (!executionReady || !photoFile || !task.execution) {
      setError(c("This task is not connected to the execution service.", "当前任务尚未连接执行服务，请稍后重试。"));
      return;
    }
    setAnalyzing(true);
    setError("");
    try {
      await task.execution.uploadStep("ENVIRONMENT_PHOTO", photoFile);
      const response = await task.execution.submit();
      if (response?.status === "FAILED") {
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

  const completedChecks = task.readinessChecks || [];

  return (
    <div className="readiness-photo-task">
      <div className="readiness-section-head">
        <div>
          <span className="eyebrow">{c("PRE-CLASS ENVIRONMENT", "课前环境确认")}</span>
          <h3>{c("Take one photo using the Self-intro standard", "按 Self-intro 标准拍一张照片")}</h3>
          <p>{c("All seven items are checked one by one. The task completes when every item passes.", "系统会按 7 项标准逐项检测；全部通过后任务自动完成。")}</p>
        </div>
        <Camera size={28} weight="fill" />
      </div>

      <div className="readiness-guidelines">
        {criteria.map(([title, detail], index) => (
          <article key={title}>
            <span>{String(index + 1).padStart(2, "0")}</span>
            <div><strong>{title}</strong><p>{detail}</p></div>
          </article>
        ))}
      </div>

      {coursewareRequired && (
        <section className="readiness-complete" aria-label={c("Lesson preparation", "首课备课")}>
          <CheckCircle size={30} weight="duotone" />
          <div>
            <strong>{c("Confirm lesson preparation", "确认已完成备课")}</strong>
            <p>{coursewareItem?.[language === "zh" ? "labelZh" : "label"]
              || coursewareItem?.label
              || c("Review the lesson slides and finish preparing before class.", "浏览全部课件，并在上课前完成备课。")}</p>
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

      <section className="readiness-complete" aria-label={c("Device and network check", "设备与网络检查")}>
        <Monitor size={30} weight="duotone" />
        <div>
          <strong>{c("Camera, microphone and network", "摄像头、麦克风和网络")}</strong>
          <p>{devicePassed
            ? c("All three checks passed.", "三项检测均已通过。")
            : c("Run the three checks before taking the environment photo.", "请先完成三项检测，再拍摄授课环境照片。")}</p>
          {Object.keys(deviceResults).length > 0 && (
            <small>{Object.entries(deviceResults).map(([key, value]) => `${key}: ${value}`).join(" · ")}</small>
          )}
        </div>
        {!devicePassed && <button className="secondary-button" type="button" disabled={checkingDevice} onClick={runDeviceCheck}>{checkingDevice ? c("Checking…", "检测中…") : c("Run checks", "开始检测")}</button>}
      </section>

      <p className="readiness-boundary-note">
        <WarningCircle size={18} weight="fill" />
        {c("Camera, microphone, network and the teaching-environment photo are all completed inside this task.", "摄像头、麦克风、网络和授课环境照片均在本任务内完成。")}
      </p>

      {completedChecks.length > 0 && (
        <div className="readiness-result-list">
          {completedChecks.map((check) => (
            <article key={check.title}><span><Check size={16} weight="bold" /></span><strong>{check.title}</strong><em>{c("Passed", "通过")}</em></article>
          ))}
        </div>
      )}

      {task.status === "completed" ? (
        <div className="readiness-complete" role="status">
          <SealCheck size={30} weight="fill" />
          <div><strong>{c("Your pre-class teaching view passed", "课前授课画面已通过")}</strong><p>{c("The check result has been recorded. No additional photo is needed.", "检测结果已经记录，无需重复拍照。")}</p></div>
        </div>
      ) : task.status === "verifying" ? (
        <div className="readiness-complete" role="status">
          <HourglassMedium size={30} weight="fill" />
          <div><strong>{c("AI review is in progress", "AI 正在审核")}</strong><p>{c("The photo is saved. No repeated submission is needed while the result is updating.", "照片已经保存，结果更新期间无需重复提交。")}</p></div>
        </div>
      ) : (
        <>
          {error && <div className="readiness-error" role="alert"><WarningCircle size={20} weight="fill" />{error}</div>}
          <section className="readiness-capture">
            {cameraOpen ? (
              <div className="readiness-live">
                <div className="readiness-camera-stage">
                  <video ref={videoRef} autoPlay muted playsInline onCanPlay={() => setCameraReady(true)} />
                  <div className="readiness-person-guide" aria-hidden="true">
                    <svg viewBox="0 0 640 360"><ellipse cx="320" cy="105" rx="45" ry="58" /><path d="M190 310 C198 242 236 202 287 187 C296 184 302 177 304 166 M336 166 C338 177 344 184 353 187 C404 202 442 242 450 310" /></svg>
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
                <span><CheckCircle size={18} weight="fill" />{c("Photo ready", "照片已拍摄")}</span>
                <button className="secondary-button" type="button" onClick={() => { if (photo.startsWith("blob:")) URL.revokeObjectURL(photo); setPhoto(""); setPhotoFile(null); }}>{c("Retake", "重新拍摄")}</button>
              </div>
            ) : (
              <div className="readiness-empty">
                <div className="readiness-example">
                  <img src={SELF_INTRO_REFERENCE_PHOTO} alt={c("Qualified Self-intro example", "Self-intro 合格示例")} />
                  <span>{c("Qualified example", "合格示例")}</span>
                </div>
                <div className="readiness-empty-copy">
                  <Camera size={38} weight="duotone" />
                  <strong>{c("Take the photo in your real teaching position", "在真实授课位置拍摄")}</strong>
                  <small>{c("16:9 landscape, upper body visible from the chest up, with a teaching headset", "16:9 横向，完整拍到胸部以上，并佩戴带麦耳麦")}</small>
          <button className="primary-button" type="button" disabled={opening || !devicePassed || !coursewareConfirmed} onClick={openCamera}><Camera size={18} weight="fill" />{opening ? c("Opening camera…", "正在打开摄像头…") : c("Open camera", "打开相机拍照")}</button>
                </div>
              </div>
            )}
          </section>
          <button className="primary-button wide-button" type="button" disabled={!photoFile || analyzing || !devicePassed || !coursewareConfirmed} onClick={submit}>
            {analyzing ? c("Checking the seven items…", "正在逐项检测…") : c("Submit photo and start check", "提交照片并开始检测")}
          </button>
          <p className="readiness-privacy"><ShieldCheck size={17} weight="fill" />{c("Use your real teaching environment and keep private information out of view.", "请使用真实授课环境拍摄，并避免在画面中暴露个人隐私信息。")}</p>
        </>
      )}
    </div>
  );
}
