import { useEffect, useRef, useState } from 'react';
import {
  Camera,
  CaretDown,
  Check,
  CheckCircle,
  SealCheck,
  WarningCircle,
} from '@phosphor-icons/react';
import {
  downloadTeacherPhoto,
  getTeacherPhoto,
  submitTeacherPhoto,
} from '../../api/teacher-photo-api';
import { localizeApiError } from '../../api-error-copy';
import { useI18n } from '../../i18n';
import { publicAsset } from '../../public-assets';
import ReadinessExampleGallery from './ReadinessExampleGallery';
import './readiness-photo-task.css';

const REFERENCE_PHOTO = publicAsset(
  '/readiness/lesson-preparation-examples/camera-angle-good-front.jpg',
);

const standards = {
  en: [
    ['camera_angle', 'Camera angle', 'Show exactly one teacher with the full unobstructed face and chest-up upper body clearly visible. Keep your head centered with a little space above it and the camera near eye level.'],
    ['lighting', 'Lighting', 'Both sides of your face and facial features must be clearly visible in even front light. Darkness, heavy shadows, overexposure, strong backlight or masking filters do not pass.'],
    ['background', 'Background', 'Use a clean, stable background with no unrelated people, animals, obvious clutter or identifiable private information. Virtual backgrounds must not break or cover you.'],
    ['dressing', 'Dressing', 'Your shoulders, neckline and neat professional top must be clearly visible. Sleepwear, loungewear, sleeveless or overly casual clothing and distracting accessories do not pass.'],
  ],
  zh: [
    ['camera_angle', '摄像头角度', '画面只保留一位老师，面部无遮挡且清晰完整，头顶留少量空间，胸部以上入镜；人物居中，镜头与眼睛大致平齐。'],
    ['lighting', '光线', '面部两侧与五官都必须清楚可辨，正面光线均匀；过暗、重阴影、过曝、强逆光或遮盖五官的滤镜不通过。'],
    ['background', '背景', '背景整洁稳定，无无关人员、动物、明显杂物或可识别的隐私信息；虚拟背景不得破损、穿帮或遮挡人物。'],
    ['dressing', '着装', '肩颈和整洁、专业的上衣必须清楚可见；睡衣、居家服、无袖、明显过于休闲或干扰性强的服饰不通过。'],
  ],
};

export default function TeacherPhotoStep({ task, step, disabled, ensureStarted, onReady, reportError }) {
  const { language } = useI18n();
  const c = (en, zh) => (language === 'zh' ? zh : en);
  const criteria = standards[language] || standards.en;
  const videoRef = useRef(null);
  const streamRef = useRef(null);
  const timerRef = useRef(null);
  const mountedRef = useRef(true);
  const photoTakenRef = useRef(false);
  const uploadAttemptRef = useRef(0);
  const [open, setOpen] = useState(false);
  const [cameraReady, setCameraReady] = useState(false);
  const [portrait, setPortrait] = useState(false);
  const [opening, setOpening] = useState(false);
  const [delay, setDelay] = useState(0);
  const [countdown, setCountdown] = useState(0);
  const [photo, setPhoto] = useState(null);
  const [preview, setPreview] = useState('');
  const [analysis, setAnalysis] = useState(null);
  const [submitting, setSubmitting] = useState(false);
  const [standardsOpen, setStandardsOpen] = useState(false);
  const [error, setError] = useState('');

  const stopCamera = () => {
    if (timerRef.current) window.clearInterval(timerRef.current);
    timerRef.current = null;
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
    if (videoRef.current) videoRef.current.srcObject = null;
    if (mountedRef.current) {
      setOpen(false);
      setCameraReady(false);
      setCountdown(0);
    }
  };

  const showFinalPhoto = async () => {
    const blob = await downloadTeacherPhoto(task.backendId);
    if (!mountedRef.current) return;
    setPreview((current) => {
      if (current.startsWith('blob:')) URL.revokeObjectURL(current);
      return URL.createObjectURL(blob);
    });
  };

  useEffect(() => {
    mountedRef.current = true;
    const controller = new AbortController();
    getTeacherPhoto(task.backendId, controller.signal)
      .then(async (result) => {
        if (controller.signal.aborted) return;
        setAnalysis(result);
        if (result.status === 'READY') {
          await onReady?.(result);
          await showFinalPhoto().catch(() => undefined);
        }
      })
      .catch(() => undefined);
    return () => {
      mountedRef.current = false;
      controller.abort();
      stopCamera();
      if (preview.startsWith('blob:')) URL.revokeObjectURL(preview);
    };
  }, [task.backendId]);

  useEffect(() => {
    if (!open || !videoRef.current || !streamRef.current) return;
    videoRef.current.srcObject = streamRef.current;
    videoRef.current.play().catch(() => {
      task.execution?.track?.('CAMERA_FAILED', {
        stepKey: step.stepKey,
        stepType: step.type,
        errorCode: 'CAMERA_PREVIEW_FAILED',
        result: 'FAILURE',
      });
      setError(c('The camera preview could not start.', '摄像头画面无法播放，请刷新页面后重试。'));
      stopCamera();
    });
  }, [open, language]);

  const openCamera = async () => {
    setError('');
    if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) {
      task.execution?.track?.('CAMERA_PERMISSION_RESULT', {
        stepKey: step.stepKey,
        stepType: step.type,
        permission: !window.isSecureContext ? 'INSECURE_CONTEXT' : 'UNSUPPORTED',
        result: 'FAILURE',
      });
      task.execution?.track?.('CAMERA_FAILED', {
        stepKey: step.stepKey,
        stepType: step.type,
        errorCode: !window.isSecureContext ? 'CAMERA_INSECURE_CONTEXT' : 'CAMERA_UNSUPPORTED',
        result: 'FAILURE',
      });
      setError(c('Use an HTTPS page in Safari, Chrome or Edge to open the camera.', '网页摄像头需要 HTTPS，请使用最新版 Safari、Chrome 或 Edge。'));
      return;
    }
    setOpening(true);
    try {
      await ensureStarted();
      stopCamera();
      streamRef.current = await navigator.mediaDevices.getUserMedia({
        audio: false,
        video: {
          facingMode: { ideal: 'user' },
          width: { ideal: 1920 },
          height: { ideal: 1080 },
          aspectRatio: { ideal: 16 / 9 },
        },
      });
      setOpen(true);
      task.execution?.track?.('CAMERA_PERMISSION_RESULT', {
        stepKey: step.stepKey,
        stepType: step.type,
        permission: 'GRANTED',
        result: 'SUCCESS',
      });
      task.execution?.track?.('CAMERA_OPENED', {
        stepKey: step.stepKey,
        stepType: step.type,
        result: 'SUCCESS',
      });
    } catch (caught) {
      task.execution?.track?.('CAMERA_PERMISSION_RESULT', {
        stepKey: step.stepKey,
        stepType: step.type,
        permission: caught?.name === 'NotAllowedError' ? 'DENIED' : 'UNAVAILABLE',
        errorCode: caught?.code || caught?.name || 'CAMERA_OPEN_FAILED',
        result: 'FAILURE',
      });
      task.execution?.track?.('CAMERA_FAILED', {
        stepKey: step.stepKey,
        stepType: step.type,
        errorCode: caught?.code || caught?.name || 'CAMERA_OPEN_FAILED',
        result: 'FAILURE',
      });
      const message = caught?.name === 'NotAllowedError'
        ? c('Allow camera access in the browser site settings, then try again.', '请在浏览器站点设置中允许使用摄像头后重试。')
        : localizeApiError(
            caught,
            language,
            c('The camera could not start.', '摄像头暂时无法打开，请检查权限后重试。'),
          );
      setError(message);
    } finally {
      setOpening(false);
    }
  };

  const capture = async () => {
    const video = videoRef.current;
    if (!video?.videoWidth || !video?.videoHeight) return;
    if (video.videoHeight > video.videoWidth) {
      setPortrait(true);
      setError(c('Rotate the device to landscape before taking the photo.', '请将设备横放，等画面切换为横向后再拍摄。'));
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
    } else {
      sh = sw / ratio;
      sy = (video.videoHeight - sh) / 2;
    }
    const canvas = document.createElement('canvas');
    canvas.width = Math.max(16, Math.floor(Math.min(1920, sw) / 16) * 16);
    canvas.height = canvas.width * 9 / 16;
    canvas.getContext('2d')?.drawImage(video, sx, sy, sw, sh, 0, 0, canvas.width, canvas.height);
    const blob = await new Promise((resolve) => canvas.toBlob(resolve, 'image/jpeg', 0.92));
    if (!blob) {
      task.execution?.track?.('CAMERA_FAILED', {
        stepKey: step.stepKey,
        stepType: step.type,
        errorCode: 'PHOTO_GENERATION_FAILED',
        result: 'FAILURE',
      });
      setError(c('The photo could not be generated.', '照片生成失败，请重新打开摄像头拍摄。'));
      return;
    }
    task.execution?.track?.(
      photoTakenRef.current ? 'CAMERA_PHOTO_RETAKEN' : 'CAMERA_PHOTO_TAKEN',
      {
        stepKey: step.stepKey,
        stepType: step.type,
        result: 'SUCCESS',
      },
    );
    photoTakenRef.current = true;
    const file = new File([blob], `readiness-${Date.now()}.jpg`, { type: 'image/jpeg' });
    setPhoto(file);
    setPreview((current) => {
      if (current.startsWith('blob:')) URL.revokeObjectURL(current);
      return URL.createObjectURL(blob);
    });
    setAnalysis(null);
    stopCamera();
  };

  const timedCapture = () => {
    if (delay === 0) return void capture();
    let remaining = delay;
    setCountdown(remaining);
    timerRef.current = window.setInterval(() => {
      remaining -= 1;
      if (remaining <= 0) {
        window.clearInterval(timerRef.current);
        timerRef.current = null;
        setCountdown(0);
        void capture();
      } else setCountdown(remaining);
    }, 1000);
  };

  const syncFrame = () => {
    const video = videoRef.current;
    if (!video?.videoWidth || !video?.videoHeight) return;
    setCameraReady(true);
    setPortrait(video.videoHeight > video.videoWidth);
  };

  const submit = async () => {
    if (!photo || submitting) return;
    setSubmitting(true);
    setError('');
    uploadAttemptRef.current += 1;
    const uploadAttempt = uploadAttemptRef.current;
    const uploadStartedAt = performance.now();
    task.execution?.track?.(uploadAttempt > 1 ? 'UPLOAD_RETRIED' : 'UPLOAD_STARTED', {
      stepKey: step.stepKey,
      stepType: step.type,
      retryNo: uploadAttempt,
      contentType: photo.type || 'image/jpeg',
      result: 'STARTED',
    });
    try {
      await ensureStarted();
      const result = await submitTeacherPhoto(task.backendId, photo);
      task.execution?.track?.('UPLOAD_SUCCEEDED', {
        stepKey: step.stepKey,
        stepType: step.type,
        retryNo: uploadAttempt,
        contentType: photo.type || 'image/jpeg',
        durationMs: Math.round(performance.now() - uploadStartedAt),
        result: 'SUCCESS',
      });
      setAnalysis(result);
      if (result.status === 'READY') {
        await onReady?.(result);
        await showFinalPhoto().catch(() => undefined);
      }
    } catch (caught) {
      task.execution?.track?.('UPLOAD_FAILED', {
        stepKey: step.stepKey,
        stepType: step.type,
        retryNo: uploadAttempt,
        contentType: photo.type || 'image/jpeg',
        durationMs: Math.round(performance.now() - uploadStartedAt),
        errorCode: caught?.code || caught?.name || 'UPLOAD_FAILED',
        result: 'FAILURE',
      });
      const message = localizeApiError(
        caught,
        language,
        c('The photo could not be checked.', '照片暂时无法检测，请稍后重试。'),
      );
      setError(message);
      reportError?.(caught);
    } finally {
      setSubmitting(false);
    }
  };

  const approved = analysis?.decision === 'PASS';
  const ready = analysis?.status === 'READY';

  return (
    <section className={`readiness-photo-task integrated-step ${disabled ? 'is-disabled' : ''}`}>
      <div className="readiness-section-head">
        <div>
          <span className="eyebrow">{c('LIVE CAMERA CHECK', '实时摄像头画面检测')}</span>
          <h3>{step.title}</h3>
          <p>{c('Take one live camera photo. The same image checks camera angle, lighting, background and dressing.', '拍一张实时摄像头照片；同一张图会检测机位、光线、背景和着装。')}</p>
        </div>
        <Camera size={28} weight="fill" />
      </div>

      <button className={`readiness-standards-toggle ${standardsOpen ? 'is-open' : ''}`} type="button" onClick={() => setStandardsOpen((value) => !value)}>
        <span>{c('4 camera-view checks', '4 项画面检测')}</span>
        <small>{standardsOpen ? c('Collapse', '收起') : c('Tap to view', '点击查看')}</small>
        <CaretDown size={18} weight="bold" />
      </button>
      <div className={`readiness-guidelines ${standardsOpen ? 'is-open' : 'is-collapsed'}`}>
        {criteria.map(([id, title, detail], index) => (
          <article key={id}><span>{String(index + 1).padStart(2, '0')}</span><div><strong>{title}</strong><p>{detail}</p></div></article>
        ))}
      </div>
      <p className="readiness-boundary-note"><WarningCircle size={18} weight="fill" />{c('Strict check: use a 16:9 landscape photo of at least 640×360. All four items need at least 85% confidence to pass. Microphone, speaker, network and device performance are checked separately.', '严格标准：使用至少 640×360 的 16:9 横向照片；四项必须全部通过，且每项判断置信度不低于 85%。麦克风、扬声器、网络和设备性能另行检测。')}</p>

      <ReadinessExampleGallery />

      {analysis?.checks?.length > 0 && (
        <div className="readiness-result-list">
          {analysis.checks.map((check) => (
            <article className={`result-${check.status}`} key={check.id}>
              <span>{check.status === 'pass' ? <Check size={16} weight="bold" /> : <WarningCircle size={16} weight="fill" />}</span>
              <div><strong>{check.title}</strong><small>{check.message}</small>{check.suggestion && <p>{check.suggestion}</p>}</div>
              <em>{check.status === 'pass' ? c('Passed', '通过') : check.status === 'fail' ? c('Adjust', '需调整') : c('Uncertain', '无法判断')}</em>
            </article>
          ))}
        </div>
      )}
      {error && <div className="readiness-error" role="alert"><WarningCircle size={20} weight="fill" />{error}</div>}

      {approved ? (
        <div className="readiness-complete" role="status">
          <SealCheck size={30} weight="fill" />
          <div>
            <small>{c('Camera check complete', '画面检测已完成')}</small>
            <strong>{c('All four visible items passed', '四项画面内容均已通过')}</strong>
            <p>{ready ? c('The check evidence has been saved. No additional photo step is required.', '检测证据已保存，不需要再完成其他拍照步骤。') : c('The result passed. The evidence is being saved.', '检测已经通过，正在保存检测证据。')}</p>
            {ready && preview && <img className="readiness-final-photo" src={preview} alt={c('Saved camera-check evidence', '已保存的画面检测证据')} />}
          </div>
        </div>
      ) : (
        <>
          {analysis?.decision === 'RETRY' && <div className="readiness-retry-note"><WarningCircle size={20} weight="fill" /><span><strong>{c('Adjust failed items, then retake', '请按未通过项调整后重拍')}</strong>{analysis.teacherMessage}</span></div>}
          <section className={open ? 'readiness-capture camera-active' : preview ? 'readiness-capture has-photo' : 'readiness-capture'}>
            {open ? (
              <div className="readiness-live">
                <div className="readiness-camera-stage">
                  <video ref={videoRef} autoPlay muted playsInline onLoadedMetadata={syncFrame} onCanPlay={syncFrame} onResize={syncFrame} />
                  <div className="readiness-person-guide" aria-hidden="true"><svg viewBox="0 0 640 360"><ellipse cx="320" cy="105" rx="45" ry="58" /><path d="M190 310 C198 242 236 202 287 187 C296 184 302 177 304 166 M336 166 C338 177 344 184 353 187 C404 202 442 242 450 310" /></svg></div>
                  {countdown > 0 && <div className="readiness-countdown"><strong>{countdown}</strong><span>{c('Look at the camera', '看向镜头，保持自然姿势')}</span></div>}
                </div>
                <div className="readiness-camera-actions readiness-camera-controls">
                  <label className="readiness-camera-timer"><span>{c('Timer', '倒计时')}</span><select value={delay} onChange={(event) => setDelay(Number(event.target.value))}><option value={0}>{c('Now', '立即')}</option><option value={3}>3 {c('sec', '秒')}</option><option value={5}>5 {c('sec', '秒')}</option><option value={10}>10 {c('sec', '秒')}</option></select></label>
                  <button className="primary-button" type="button" disabled={!cameraReady || portrait || countdown > 0} onClick={timedCapture}><Camera size={18} weight="fill" />{portrait ? c('Rotate device', '请横放设备') : c('Take photo', '立即拍照')}</button>
                  <button className="secondary-button" type="button" onClick={stopCamera}>{c('Cancel', '取消')}</button>
                </div>
              </div>
            ) : preview ? (
              <div className="readiness-photo-preview"><img src={preview} alt={c('Photo ready for checking', '待检测照片')} /><span><CheckCircle size={18} weight="fill" />{c('Photo ready', '照片已拍摄')}</span><button className="secondary-button" type="button" onClick={openCamera}>{c('Retake', '重新拍摄')}</button></div>
            ) : (
              <div className="readiness-empty">
                <div className="readiness-example"><img src={REFERENCE_PHOTO} alt={c('Camera framing reference', '摄像头画面参考')} /><span>{c('Checklist example · headset not checked', '清单合格示例 · 不检测耳麦')}</span></div>
                <div className="readiness-empty-copy"><Camera size={38} weight="duotone" /><strong>{c('Open your current teaching camera view', '打开当前授课摄像头画面')}</strong><small>{c('One photo checks camera angle, lighting, background and dressing', '一张照片同时检测机位、光线、背景和着装')}</small><button className="primary-button" type="button" disabled={disabled || opening} onClick={openCamera}><Camera size={18} weight="fill" />{opening ? c('Opening camera…', '正在打开摄像头…') : c('Open camera', '打开摄像头')}</button></div>
              </div>
            )}
          </section>
          {photo && <button className="primary-button wide-button" type="button" disabled={submitting} onClick={submit}>{submitting ? c('Checking four items…', '正在检测四项内容…') : c('Submit and check 4 items', '提交并检测四项内容')}</button>}
        </>
      )}
    </section>
  );
}
