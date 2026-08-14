import { publicAsset } from "../../public-assets";

export const TEACHING_ENVIRONMENT_REFERENCE_PHOTO = publicAsset(
  "/readiness/lesson-preparation-examples/camera-angle-good-front.jpg",
);

export const TEACHING_ENVIRONMENT_STANDARDS = {
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

export const teachingEnvironmentCameraErrors = (c) => ({
  NotAllowedError: c(
    "Camera permission was not granted. Allow camera access in the browser site settings, then try again.",
    "未获得摄像头权限。请在浏览器地址栏的站点设置中允许使用摄像头后重试。",
  ),
  NotFoundError: c(
    "No camera was found. Check that a camera is available, then try again.",
    "没有检测到可用摄像头，请确认设备摄像头可用后重试。",
  ),
  NotReadableError: c(
    "The camera is being used by another app. Close that app, then try again.",
    "摄像头正被其他程序占用，请关闭占用程序后重试。",
  ),
});
